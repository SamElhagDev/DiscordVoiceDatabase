"""
Per-user segmented voice recorder.

Subscribes to each consented user's audio stream in a voice channel,
writes rolling PCM segments to disk, and indexes them in SQLite.
A background task remuxes raw PCM files to OGG/Opus for storage efficiency.
"""

import array
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


FRAME_SIZE_SAMPLES = 960  # 20ms at 48kHz
FRAME_BYTES = FRAME_SIZE_SAMPLES * CHANNELS * SAMPLE_WIDTH  # 3840 bytes per frame
SILENCE_FRAME = b"\x00" * FRAME_BYTES
MAX_PLC_FRAMES = 10       # ~200ms: Opus PLC degrades but still beats hard silence
PLC_FADE_FRAMES = 3       # fade out last 60ms of PLC before silence boundary
JITTER_DEPTH = 12         # 240ms reorder window — recording has no latency cost
DECODER_RESET_THRESHOLD = 5  # consecutive decode failures before nuking decoder state


def _seq_delta(a: int, b: int) -> int:
    """Signed distance b - a in 16-bit RTP sequence space."""
    d = (b - a) & 0xFFFF
    return d if d < 0x8000 else d - 0x10000


def _scale_pcm(pcm: bytes, factor: float) -> bytes:
    """Scale PCM sample amplitudes by *factor* for fade-out."""
    samples = array.array("h", pcm)
    for i in range(len(samples)):
        samples[i] = int(samples[i] * factor)
    return samples.tobytes()


class _JitterBuffer:
    """
    Per-user reorder buffer.  Holds incoming Opus packets for JITTER_DEPTH
    frames so that late arrivals slot into sequence order before gap-fill
    logic runs.

    Recording context — latency is free, so a generous buffer window avoids
    treating mobile jitter spikes as packet loss.
    """

    __slots__ = ("_depth", "_pkts", "_base")

    def __init__(self, depth: int = JITTER_DEPTH):
        self._depth = depth
        self._pkts: dict[int, bytes] = {}
        self._base: int | None = None

    def push(self, seq: int, opus_bytes: bytes) -> list[tuple[int, bytes | None]]:
        """
        Insert a packet.  Returns a (possibly empty) list of
        (sequence, opus_bytes | None) entries ready to decode, in order.
        None entries represent confirmed lost packets.
        """
        if self._base is not None:
            delta = _seq_delta(self._base, seq)
            if delta < 0:
                return []
            if delta > 500:
                self._pkts.clear()
                self._base = seq

        self._pkts[seq] = opus_bytes

        if self._base is None:
            self._base = seq
            return []

        if _seq_delta(self._base, seq) < self._depth:
            return []

        return self._drain_to((seq - self._depth) & 0xFFFF)

    def drain(self) -> list[tuple[int, bytes | None]]:
        """Flush all remaining packets (e.g. on user silence or disconnect)."""
        if not self._pkts or self._base is None:
            return []
        farthest = max(self._pkts, key=lambda s: _seq_delta(self._base, s))
        result = self._drain_to(farthest)
        self._pkts.clear()
        self._base = None
        return result

    def _drain_to(self, target: int) -> list[tuple[int, bytes | None]]:
        out = []
        seq = self._base
        while _seq_delta(seq, target) >= 0:
            out.append((seq, self._pkts.pop(seq, None)))
            seq = (seq + 1) & 0xFFFF
        self._base = seq
        return out


class _PerUserPCMSink(voice_recv.AudioSink):
    """
    Receives per-user Opus frames from voice_recv, applies DAVE E2EE decryption
    when the channel has end-to-end encryption active, then decodes to PCM.

    A per-user jitter buffer reorders late-arriving packets (common on mobile)
    before gap-fill logic runs, so only truly lost packets trigger PLC.

    Handles confirmed packet loss by:
      1. FEC recovery (decode next packet's redundancy data for the lost one)
      2. PLC (Opus decoder extrapolates from internal state, up to ~200ms)
      3. Fade-out at PLC boundary to smooth the transition to silence
      4. Silence padding for longer gaps
    """

    _DAVE_LOG_INTERVAL = 60.0  # log DAVE fallbacks at most once per user per minute

    def __init__(self, callback):
        super().__init__()
        self._callback = callback
        self._decoders: dict[int, opus_mod.Decoder] = {}
        self._jitter: dict[int, _JitterBuffer] = {}
        self._consecutive_failures: dict[int, int] = {}
        self._dave_log_times: dict[int, float] = {}
        self._dave_log_counts: dict[int, int] = {}

    def wants_opus(self) -> bool:
        return True

    def write(self, user, data: voice_recv.VoiceData):
        if user is None:
            return

        from discord.ext.voice_recv.rtp import SilencePacket, FakePacket

        if isinstance(data.packet, SilencePacket):
            buf = self._jitter.get(user.id)
            if buf:
                remaining = buf.drain()
                if remaining:
                    self._process_batch(user, remaining)
            return

        if isinstance(data.packet, FakePacket):
            return

        opus_bytes = data.opus
        if not opus_bytes:
            return

        dave_session = self._get_dave_session()
        if dave_session is not None:
            if dave_session.can_passthrough(user.id):
                pass
            elif dave_session.ready:
                try:
                    opus_bytes = dave_session.decrypt(
                        user.id, _davey.MediaType.audio, opus_bytes
                    )
                except Exception as e:
                    # Decryption failed — pass raw bytes through to decoder.
                    # Common cases:
                    #   UnencryptedWhenPassthroughDisabled — valid unencrypted Opus
                    #   NoValidCryptorFound — often unencrypted despite DAVE confusion
                    # If the bytes are genuinely encrypted garbage, the Opus decoder
                    # will fail and the resilient recovery handles it gracefully.
                    if logger.isEnabledFor(logging.DEBUG):
                        self._log_dave_fallback(user.id, e)
            else:
                return

        seq = getattr(data.packet, "sequence", None)
        if seq is None:
            decoder = self._get_decoder(user.id)
            try:
                pcm = decoder.decode(opus_bytes, fec=False)
                self._callback(user, pcm)
            except Exception:
                pass
            return

        buf = self._jitter.get(user.id)
        if buf is None:
            buf = _JitterBuffer()
            self._jitter[user.id] = buf

        ready = buf.push(seq, opus_bytes)
        if ready:
            self._process_batch(user, ready)

    def _process_batch(self, user, batch: list[tuple[int, bytes | None]]):
        """Decode a run of jitter-buffer output: real packets and gaps."""
        decoder = self._get_decoder(user.id)

        i = 0
        n = len(batch)
        while i < n:
            _seq, opus = batch[i]

            if opus is not None:
                try:
                    pcm = decoder.decode(opus, fec=False)
                    self._callback(user, pcm)
                    self._consecutive_failures[user.id] = 0
                except Exception as e:
                    fails = self._consecutive_failures.get(user.id, 0) + 1
                    self._consecutive_failures[user.id] = fails

                    if fails >= DECODER_RESET_THRESHOLD:
                        # Genuine stream break — reset decoder and counter
                        decoder = opus_mod.Decoder()
                        self._decoders[user.id] = decoder
                        self._consecutive_failures[user.id] = 0
                        if logger.isEnabledFor(logging.DEBUG):
                            logger.debug(
                                f"Opus decoder reset for user {user.id} seq {_seq}: "
                                f"{fails} consecutive failures"
                            )
                        # Fresh decoder has no state — silence is all we can do
                        self._callback(user, SILENCE_FRAME)
                    else:
                        if logger.isEnabledFor(logging.DEBUG):
                            logger.debug(
                                f"Opus decode failed user {user.id} seq {_seq}: {e} "
                                f"— PLC fill ({fails}/{DECODER_RESET_THRESHOLD})"
                            )
                        # Decoder state intact — PLC extrapolates smoothly
                        try:
                            plc_pcm = decoder.decode(None, fec=False)
                            self._callback(user, plc_pcm)
                        except Exception:
                            self._callback(user, SILENCE_FRAME)
                i += 1
                continue

            gap_start = i
            while i < n and batch[i][1] is None:
                i += 1
            gap = i - gap_start

            next_opus = batch[i][1] if i < n else None
            self._fill_gap(user, decoder, gap, next_opus)

    def _fill_gap(
        self,
        user,
        decoder: opus_mod.Decoder,
        gap: int,
        next_opus: bytes | None,
    ):
        """Generate PCM for lost packets using FEC, PLC with fade, and silence."""
        filled = 0

        if next_opus is not None:
            try:
                fec_pcm = decoder.decode(next_opus, fec=True)
                self._callback(user, fec_pcm)
                filled = 1
            except Exception:
                pass

        plc_count = min(gap - filled, MAX_PLC_FRAMES)
        needs_fade = (gap - filled) > MAX_PLC_FRAMES
        fade_start = plc_count - PLC_FADE_FRAMES if needs_fade else plc_count

        for j in range(plc_count):
            try:
                plc_pcm = decoder.decode(None, fec=False)
                if needs_fade and j >= fade_start:
                    fade_pos = j - fade_start + 1
                    factor = 1.0 - (fade_pos / (PLC_FADE_FRAMES + 1))
                    plc_pcm = _scale_pcm(plc_pcm, factor)
                self._callback(user, plc_pcm)
                filled += 1
            except Exception:
                break

        remaining = gap - filled
        if remaining > 0:
            self._callback(user, SILENCE_FRAME * remaining)

    def _log_dave_fallback(self, user_id: int, error: Exception):
        """Rate-limited DAVE fallback logging — once per user per minute."""
        now = time.monotonic()
        self._dave_log_counts[user_id] = self._dave_log_counts.get(user_id, 0) + 1
        last = self._dave_log_times.get(user_id, 0.0)
        if now - last < self._DAVE_LOG_INTERVAL:
            return
        count = self._dave_log_counts[user_id]
        self._dave_log_times[user_id] = now
        self._dave_log_counts[user_id] = 0
        err_type = "unencrypted" if "Unencrypted" in str(error) else str(error)[:80]
        logger.debug(
            f"DAVE fallback for user {user_id}: {err_type} "
            f"({count} packets since last log)"
        )

    def _get_decoder(self, user_id: int) -> opus_mod.Decoder:
        d = self._decoders.get(user_id)
        if d is None:
            d = opus_mod.Decoder()
            self._decoders[user_id] = d
        return d

    def _get_dave_session(self):
        vc = self._voice_client
        conn = getattr(vc, "_connection", None) if vc else None
        return getattr(conn, "dave_session", None) if conn else None

    def cleanup(self):
        self._jitter.clear()
        self._decoders.clear()
        self._consecutive_failures.clear()
        self._dave_log_times.clear()
        self._dave_log_counts.clear()


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
        self._last_packet_time: float = 0.0

    async def start(self, voice_client: voice_recv.VoiceRecvClient):
        """Begin recording all consented users in the channel."""
        self.voice_client = voice_client
        self.consented_users = await self.db.get_consented_user_ids(self.guild.id)
        self._running = True
        self._last_packet_time = time.monotonic()

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

    @property
    def seconds_since_last_packet(self) -> float:
        if self._last_packet_time == 0.0:
            return 0.0
        return time.monotonic() - self._last_packet_time

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
        self._last_packet_time = time.monotonic()

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
        rotate_start = time.time()
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

        rotate_dur = time.time() - rotate_start
        audio_dur = end_ts - start_ts
        if audio_dur > 0:
            await self.db.log_perf(segment_id, "rotate", rotate_dur, audio_dur)

        await self._remux_queue.put((pcm_path, ogg_path, segment_id, audio_dur))

        logger.debug(
            f"Segment rotated: user={user_id} duration={elapsed:.1f}s size={file_size}"
        )

    async def _remux_worker(self):
        try:
            while True:
                pcm_path, ogg_path, segment_id, audio_dur = await self._remux_queue.get()
                try:
                    remux_start = time.time()
                    await asyncio.to_thread(self._remux_pcm_to_ogg, pcm_path, ogg_path)
                    remux_dur = time.time() - remux_start
                    if os.path.exists(pcm_path):
                        os.remove(pcm_path)
                    ogg_size = os.path.getsize(ogg_path) if os.path.exists(ogg_path) else 0
                    if ogg_size > 0:
                        await self.db.update_segment_file_size(segment_id, ogg_size)
                        if audio_dur > 0:
                            await self.db.log_perf(segment_id, "remux", remux_dur, audio_dur)
                        if self.transcriber:
                            await self.transcriber.enqueue(ogg_path, segment_id, audio_dur)
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
