"""
Per-user segmented voice recorder.

Subscribes to each consented user's audio stream in a voice channel,
writes rolling PCM segments to disk, and indexes them in SQLite.
A background task remuxes raw PCM files to OGG/Opus for storage efficiency.
"""

import asyncio
import io
import logging
import os
import struct
import subprocess
import time
from pathlib import Path

import discord
import discord.opus as opus_mod
from discord.ext import voice_recv

try:
    import davey as _davey
except ImportError:
    _davey = None

logger = logging.getLogger("discord_bot")

# PCM settings from discord.py voice receive: 48kHz, stereo, 16-bit signed LE
SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 2  # 16-bit
BYTES_PER_SEC = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH  # 192,000 bytes/sec


class UserStream:
    """Tracks a single user's current recording segment."""

    def __init__(self, user_id: int, guild_id: int, channel_id: int, base_path: str):
        self.user_id = user_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.start_ts = time.time()
        self.buffer = io.BytesIO()
        self.segment_db_id = None

        # Build file path: recordings/<guild_id>/<user_id>/<timestamp>.pcm
        ts_ms = int(self.start_ts * 1000)
        self.directory = os.path.join(base_path, str(guild_id), str(user_id))
        os.makedirs(self.directory, exist_ok=True)
        self.pcm_path = os.path.join(self.directory, f"{ts_ms}.pcm")
        self.ogg_path = os.path.join(self.directory, f"{ts_ms}.ogg")

    def write(self, data: bytes):
        self.buffer.write(data)

    def flush_to_disk(self) -> str:
        """Write buffer to PCM file and return the path."""
        if self.buffer.tell() == 0:
            return None
        self.buffer.seek(0)
        with open(self.pcm_path, "wb") as f:
            f.write(self.buffer.read())
        return self.pcm_path

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_ts

    @property
    def has_data(self) -> bool:
        return self.buffer.tell() > 0


class _PerUserPCMSink(voice_recv.AudioSink):
    """
    Receives per-user Opus frames from voice_recv, applies DAVE E2EE decryption
    when the channel has end-to-end encryption active, then decodes to PCM.
    """

    def __init__(self, callback):
        super().__init__()
        self._callback = callback
        self._decoders: dict[int, opus_mod.Decoder] = {}

    def wants_opus(self) -> bool:
        # Take raw (possibly DAVE-encrypted) Opus bytes; we do Opus→PCM ourselves.
        return True

    def write(self, user, data: voice_recv.VoiceData):
        if user is None:
            return

        from discord.ext.voice_recv.rtp import SilencePacket, FakePacket
        opus_bytes = data.opus
        if not opus_bytes:
            return

        # Silence/FEC-fill packets are internally generated — no DAVE layer.
        if not isinstance(data.packet, (SilencePacket, FakePacket)):
            dave_session = self._get_dave_session()
            if dave_session is not None and dave_session.ready and not dave_session.can_passthrough:
                try:
                    opus_bytes = dave_session.decrypt(user.id, _davey.MediaType.audio, opus_bytes)
                except Exception as e:
                    logger.debug(f"DAVE decrypt failed for user {user.id}: {e}")
                    return

        decoder = self._decoders.get(user.id)
        if decoder is None:
            decoder = opus_mod.Decoder()
            self._decoders[user.id] = decoder

        try:
            pcm = decoder.decode(opus_bytes, fec=False)
        except Exception as e:
            logger.debug(f"Opus decode failed for user {user.id}: {e}")
            return

        self._callback(user, pcm)

    def _get_dave_session(self):
        vc = self._voice_client
        conn = getattr(vc, "_connection", None) if vc else None
        return getattr(conn, "dave_session", None) if conn else None

    def cleanup(self):
        self._decoders.clear()


class VoiceRecorder:
    """
    Manages per-user audio recording for a single voice channel connection.

    Usage:
        recorder = VoiceRecorder(bot, guild, channel, db, recordings_path, segment_duration)
        await recorder.start(voice_client)
        ...
        await recorder.stop()
    """

    def __init__(
        self,
        bot,
        guild: discord.Guild,
        channel: discord.VoiceChannel,
        database,
        recordings_path: str = "recordings",
        segment_duration_sec: int = 60,
    ):
        self.bot = bot
        self.guild = guild
        self.channel = channel
        self.db = database
        self.recordings_path = recordings_path
        self.segment_duration_sec = segment_duration_sec
        self.user_streams: dict[int, UserStream] = {}
        self.consented_users: set[int] = set()
        self._running = False
        self._rotation_task: asyncio.Task = None
        self._remux_queue: asyncio.Queue = asyncio.Queue()
        self._remux_task: asyncio.Task = None

    async def start(self, voice_client: voice_recv.VoiceRecvClient):
        """Begin recording all consented users in the channel."""
        self.voice_client = voice_client
        self.consented_users = await self.db.get_consented_user_ids(self.guild.id)
        self._running = True

        sink = _PerUserPCMSink(self.on_audio_packet)
        self.voice_client.listen(sink)

        self._rotation_task = asyncio.create_task(self._rotation_loop())
        self._remux_task = asyncio.create_task(self._remux_worker())

        logger.info(f"Recording started in {self.guild.name} / #{self.channel.name}")

    async def stop(self):
        """Stop recording and flush all segments."""
        self._running = False

        if self._rotation_task:
            self._rotation_task.cancel()
            try:
                await self._rotation_task
            except asyncio.CancelledError:
                pass

        try:
            if self.voice_client and self.voice_client.is_listening():
                self.voice_client.stop_listening()
        except Exception as e:
            logger.warning(f"Error stopping recording: {e}")

        # Flush all open segments
        for user_id in list(self.user_streams.keys()):
            await self._rotate_user(user_id, final=True)

        # Wait for remux queue to drain
        await self._remux_queue.join()
        if self._remux_task:
            self._remux_task.cancel()
            try:
                await self._remux_task
            except asyncio.CancelledError:
                pass

        self.user_streams.clear()
        logger.info(f"Recording stopped in {self.guild.name} / #{self.channel.name}")

    async def refresh_consent(self):
        """Refresh the consented user set from DB (call periodically or on user join/leave)."""
        self.consented_users = await self.db.get_consented_user_ids(self.guild.id)

    def on_audio_packet(self, user: discord.User, data: bytes):
        """
        Called for each audio packet received from a user.
        discord.py PCMSink delivers decoded PCM frames here.
        Only record if user has consented.
        """
        if user.id not in self.consented_users:
            return

        stream = self.user_streams.get(user.id)
        if stream is None:
            stream = UserStream(
                user_id=user.id,
                guild_id=self.guild.id,
                channel_id=self.channel.id,
                base_path=self.recordings_path,
            )
            self.user_streams[user.id] = stream

        stream.write(data)

    async def _rotation_loop(self):
        """Periodically rotate segments for all active users."""
        try:
            while self._running:
                await asyncio.sleep(5)  # check every 5 seconds
                for user_id in list(self.user_streams.keys()):
                    stream = self.user_streams.get(user_id)
                    if stream and stream.elapsed >= self.segment_duration_sec:
                        await self._rotate_user(user_id)
        except asyncio.CancelledError:
            pass

    async def _rotate_user(self, user_id: int, final: bool = False):
        """Flush the current segment for a user and start a new one."""
        stream = self.user_streams.get(user_id)
        if stream is None or not stream.has_data:
            if final:
                self.user_streams.pop(user_id, None)
            return

        # Flush PCM to disk
        pcm_path = stream.flush_to_disk()
        if pcm_path is None:
            return

        end_ts = time.time()
        file_size = os.path.getsize(pcm_path) if os.path.exists(pcm_path) else 0

        # Register segment in DB (store the ogg path since we'll remux)
        segment_id = await self.db.add_segment(
            guild_id=stream.guild_id,
            channel_id=stream.channel_id,
            user_id=user_id,
            start_ts=stream.start_ts,
            file_path=stream.ogg_path,
        )
        await self.db.close_segment(segment_id, end_ts, file_size)

        # Queue for background remux
        await self._remux_queue.put((pcm_path, stream.ogg_path))

        logger.debug(
            f"Segment rotated: user={user_id} duration={stream.elapsed:.1f}s size={file_size}"
        )

        # Start new segment or remove if final
        if final:
            self.user_streams.pop(user_id, None)
        else:
            self.user_streams[user_id] = UserStream(
                user_id=user_id,
                guild_id=stream.guild_id,
                channel_id=stream.channel_id,
                base_path=self.recordings_path,
            )

    async def _remux_worker(self):
        """Background task to convert PCM segments to OGG/Opus."""
        try:
            while True:
                pcm_path, ogg_path = await self._remux_queue.get()
                try:
                    await asyncio.to_thread(self._remux_pcm_to_ogg, pcm_path, ogg_path)
                    # Remove the PCM file after successful remux
                    if os.path.exists(pcm_path):
                        os.remove(pcm_path)
                except Exception as e:
                    logger.error(f"Remux failed for {pcm_path}: {e}")
                finally:
                    self._remux_queue.task_done()
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _remux_pcm_to_ogg(pcm_path: str, ogg_path: str):
        """Convert raw PCM to OGG/Opus using ffmpeg. Runs in a thread."""
        cmd = [
            "ffmpeg", "-y",
            "-f", "s16le",
            "-ar", str(SAMPLE_RATE),
            "-ac", str(CHANNELS),
            "-i", pcm_path,
            "-c:a", "libopus",
            "-b:a", "48k",  # low bitrate, mono-equivalent for voice
            "-ac", "1",     # downmix to mono to save space
            "-application", "voip",
            ogg_path,
        ]
        result = subprocess.run(
            cmd, capture_output=True, timeout=60
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg failed ({result.returncode}): {result.stderr.decode(errors='replace')}"
            )
