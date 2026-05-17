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
import subprocess
import threading
import time

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
        self._has_data = False

        # Build file path: recordings/<guild_id>/<user_id>/<timestamp>.pcm
        ts_ms = int(self.start_ts * 1000)
        self.directory = os.path.join(base_path, str(guild_id), str(user_id))
        os.makedirs(self.directory, exist_ok=True)
        self.pcm_path = os.path.join(self.directory, f"{ts_ms}.pcm")
        self.ogg_path = os.path.join(self.directory, f"{ts_ms}.ogg")

    def write(self, data: bytes):
        self._has_data = True
        self.buffer.write(data)

    def flush_to_disk(self) -> str:
        """Write buffer to PCM file and return the path."""
        buf_size = self.buffer.tell()
        if buf_size == 0:
            logger.debug(f"UserStream flush: no data for user {self.user_id}")
            return None
        self.buffer.seek(0)
        with open(self.pcm_path, "wb") as f:
            f.write(self.buffer.read())
        logger.debug(f"UserStream flush: wrote {buf_size} bytes to {self.pcm_path}")
        return self.pcm_path

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_ts

    @property
    def has_data(self) -> bool:
        return self._has_data


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
            if dave_session is not None:
                if dave_session.can_passthrough(user.id):
                    pass  # DAVE transition window: packets are plain Opus
                elif dave_session.ready:
                    try:
                        opus_bytes = dave_session.decrypt(user.id, _davey.MediaType.audio, opus_bytes)
                    except Exception as e:
                        if "UnencryptedWhenPassthroughDisabled" in str(e):
                            # Packet is plain Opus but passthrough window closed —
                            # use it as-is rather than dropping audio.
                            logger.debug(f"DAVE passthrough fallback for user {user.id} (unencrypted packet)")
                        else:
                            logger.debug(f"DAVE decrypt failed for user {user.id}: {e}")
                            return
                else:
                    # DAVE active but MLS group not yet established — skip to avoid garbage PCM
                    return

        decoder = self._decoders.get(user.id)
        if decoder is None:
            decoder = opus_mod.Decoder()
            self._decoders[user.id] = decoder

        try:
            pcm = decoder.decode(opus_bytes, fec=False)
        except Exception as e:
            logger.debug(f"Opus decode failed for user {user.id}: {e} — resetting decoder")
            # Decoder state is corrupted (e.g. after DAVE transition);
            # replace it so the next packet starts fresh.
            self._decoders[user.id] = opus_mod.Decoder()
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
        transcriber=None,
    ):
        self.bot = bot
        self.guild = guild
        self.channel = channel
        self.db = database
        self.recordings_path = recordings_path
        self.segment_duration_sec = segment_duration_sec
        self.transcriber = transcriber
        self.user_streams: dict[int, UserStream] = {}
        self._streams_lock = threading.Lock()
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

        with self._streams_lock:
            user_ids = list(self.user_streams.keys())
        for user_id in user_ids:
            await self._rotate_user(user_id, final=True)

        await self._remux_queue.join()
        if self._remux_task:
            self._remux_task.cancel()
            try:
                await self._remux_task
            except asyncio.CancelledError:
                pass

        with self._streams_lock:
            self.user_streams.clear()
        logger.info(f"Recording stopped in {self.guild.name} / #{self.channel.name}")

    async def refresh_consent(self):
        """Refresh the consented user set from DB (call periodically or on user join/leave)."""
        old_count = len(self.consented_users)
        self.consented_users = await self.db.get_consented_user_ids(self.guild.id)
        logger.debug(f"Consent refreshed for guild {self.guild.id}: {old_count} → {len(self.consented_users)} users")

    def pause_listening(self):
        """Detach the audio sink before clip playback to avoid send/receive interference.
        Always pair with a resume_listening() call once playback is done."""
        try:
            if self.voice_client and self.voice_client.is_listening():
                self.voice_client.stop_listening()
                logger.info(
                    f"Recording paused for clip playback in "
                    f"{self.guild.name} / #{self.channel.name}"
                )
        except Exception as e:
            logger.warning(f"pause_listening: could not stop sink: {e}")

    def resume_listening(self):
        """Re-attach a fresh audio sink after clip playback ends.
        No-op if the recorder has already been stopped."""
        if not self._running:
            logger.debug("resume_listening: recorder already stopped, skipping")
            return
        try:
            if self.voice_client and self.voice_client.is_connected():
                # Create a new sink — the old one may have stale decoder state.
                sink = _PerUserPCMSink(self.on_audio_packet)
                self.voice_client.listen(sink)
                logger.info(
                    f"Recording resumed after clip playback in "
                    f"{self.guild.name} / #{self.channel.name}"
                )
            else:
                logger.warning(
                    "resume_listening: voice_client not connected — "
                    "recording stays paused until next auto-rejoin"
                )
        except Exception as e:
            logger.warning(f"resume_listening: could not re-attach sink: {e}")

    def on_audio_packet(self, user: discord.User, data: bytes):
        if user.id not in self.consented_users:
            return

        with self._streams_lock:
            stream = self.user_streams.get(user.id)
            if stream is None:
                stream = UserStream(
                    user_id=user.id,
                    guild_id=self.guild.id,
                    channel_id=self.channel.id,
                    base_path=self.recordings_path,
                )
                self.user_streams[user.id] = stream
                logger.debug(f"New audio stream opened for user {user.id} ({user.name})")

            stream.write(data)

    async def _rotation_loop(self):
        try:
            while self._running:
                await asyncio.sleep(5)
                with self._streams_lock:
                    due = [
                        uid for uid, s in self.user_streams.items()
                        if s.elapsed >= self.segment_duration_sec
                    ]
                for user_id in due:
                    await self._rotate_user(user_id)
        except asyncio.CancelledError:
            pass

    async def _rotate_user(self, user_id: int, final: bool = False):
        with self._streams_lock:
            stream = self.user_streams.get(user_id)
            if stream is None or not stream.has_data:
                if final:
                    self.user_streams.pop(user_id, None)
                return

            # Swap in a fresh stream immediately so new packets aren't blocked
            # during the disk flush below.
            if final:
                self.user_streams.pop(user_id, None)
            else:
                self.user_streams[user_id] = UserStream(
                    user_id=user_id,
                    guild_id=stream.guild_id,
                    channel_id=stream.channel_id,
                    base_path=self.recordings_path,
                )

        # Flush outside the lock — disk I/O must not block packet ingestion.
        pcm_path = stream.flush_to_disk()
        ogg_path = stream.ogg_path
        guild_id = stream.guild_id
        channel_id = stream.channel_id
        start_ts = stream.start_ts
        elapsed = stream.elapsed

        if pcm_path is None:
            return

        end_ts = time.time()
        file_size = os.path.getsize(pcm_path) if os.path.exists(pcm_path) else 0

        segment_id = await self.db.add_segment(
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=user_id,
            start_ts=start_ts,
            file_path=ogg_path,
        )
        await self.db.close_segment(segment_id, end_ts, file_size)

        await self._remux_queue.put((pcm_path, ogg_path, segment_id))

        logger.debug(
            f"Segment rotated: user={user_id} duration={elapsed:.1f}s size={file_size}"
        )

    async def _remux_worker(self):
        try:
            while True:
                pcm_path, ogg_path, segment_id = await self._remux_queue.get()
                try:
                    await asyncio.to_thread(self._remux_pcm_to_ogg, pcm_path, ogg_path)
                    if os.path.exists(pcm_path):
                        os.remove(pcm_path)
                    ogg_size = os.path.getsize(ogg_path) if os.path.exists(ogg_path) else 0
                    if ogg_size > 0:
                        await self.db.update_segment_file_size(segment_id, ogg_size)
                        if self.transcriber:
                            await self.transcriber.enqueue(ogg_path, segment_id)
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
            "-b:a", "128k",
            "-ac", "1",
            "-application", "audio",
            ogg_path,
        ]
        result = subprocess.run(
            cmd, capture_output=True, timeout=60
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg failed ({result.returncode}): {result.stderr.decode(errors='replace')}"
            )
