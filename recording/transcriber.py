"""
Background transcription worker using faster-whisper.

Transcribes OGG segments after remux and stores the text in the database.
"""

import asyncio
import logging
import os
import time

from faster_whisper import WhisperModel

logger = logging.getLogger("discord_bot")

MODEL_SIZE = os.getenv("WHISPER_MODEL", "tiny")


class Transcriber:
    def __init__(self, database):
        self.db = database
        self._model = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task = None
        self._load_task: asyncio.Task = None

    def _load_model(self):
        if self._model is None:
            logger.info(f"Loading Whisper model: {MODEL_SIZE}")
            self._model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
            logger.info("Whisper model loaded")
        return self._model

    def start(self):
        self._task = asyncio.create_task(self._worker())
        self._load_task = asyncio.create_task(self._eager_load_model())
        logger.info("Transcription worker started")

    async def _eager_load_model(self):
        """Load the Whisper model eagerly so the first transcription isn't delayed."""
        try:
            await asyncio.to_thread(self._load_model)
            logger.info("Whisper model pre-loaded successfully")
        except Exception as e:
            logger.warning(f"Eager model load failed (will retry on first transcription): {e}")

    def stop(self):
        if self._load_task:
            self._load_task.cancel()
        if self._task:
            self._task.cancel()
            logger.info("Transcription worker stopped")

    async def enqueue(self, ogg_path: str, segment_id: int, audio_duration: float = 60.0):
        await self._queue.put((ogg_path, segment_id, audio_duration))
        logger.debug(f"Enqueued transcription: segment {segment_id} ({ogg_path}) — queue size: {self._queue.qsize()}")

    async def _worker(self):
        try:
            while True:
                ogg_path, segment_id, audio_duration = await self._queue.get()
                try:
                    if not os.path.exists(ogg_path):
                        logger.warning(f"Transcription skipped — file not found: {ogg_path} (segment {segment_id})")
                        continue
                    logger.debug(f"Transcribing segment {segment_id}: {ogg_path}")
                    t0 = time.time()
                    transcript = await asyncio.to_thread(self.transcribe_file, ogg_path)
                    duration = time.time() - t0
                    result = transcript.strip() if transcript and transcript.strip() else "Blank"
                    await self.db.set_segment_transcript(segment_id, result)
                    if audio_duration > 0:
                        await self.db.log_perf(segment_id, "transcribe", duration, audio_duration)
                    logger.info(f"Transcribed segment {segment_id}: {result[:80]} ({duration:.2f}s)")
                except Exception as e:
                    logger.error(f"Transcription failed for segment {segment_id}: {e}", exc_info=True)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass

    def transcribe_file(self, audio_path: str) -> str:
        model = self._load_model()
        segments, _ = model.transcribe(
            audio_path,
            language="en",
            vad_filter=True,
            beam_size=3,
        )
        text = " ".join(seg.text.strip() for seg in segments)
        return text.strip()
