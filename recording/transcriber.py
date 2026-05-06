"""
Background transcription worker using faster-whisper.

Transcribes OGG segments after remux and stores the text in the database.
"""

import asyncio
import logging
import os

from faster_whisper import WhisperModel

logger = logging.getLogger("discord_bot")

MODEL_SIZE = os.getenv("WHISPER_MODEL", "tiny")


class Transcriber:
    def __init__(self, database):
        self.db = database
        self._model = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task = None

    def _load_model(self):
        if self._model is None:
            logger.info(f"Loading Whisper model: {MODEL_SIZE}")
            self._model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
            logger.info("Whisper model loaded")
        return self._model

    def start(self):
        self._task = asyncio.create_task(self._worker())

    def stop(self):
        if self._task:
            self._task.cancel()

    async def enqueue(self, ogg_path: str, segment_id: int):
        await self._queue.put((ogg_path, segment_id))

    async def _worker(self):
        try:
            while True:
                ogg_path, segment_id = await self._queue.get()
                try:
                    if not os.path.exists(ogg_path):
                        continue
                    transcript = await asyncio.to_thread(self._transcribe, ogg_path)
                    result = transcript.strip() if transcript and transcript.strip() else "Blank"
                    await self.db.set_segment_transcript(segment_id, result)
                    logger.debug(f"Transcribed segment {segment_id}: {result[:80]}")
                except Exception as e:
                    logger.error(f"Transcription failed for segment {segment_id}: {e}")
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass

    def _transcribe(self, audio_path: str) -> str:
        model = self._load_model()
        segments, _ = model.transcribe(
            audio_path,
            language="en",
            vad_filter=True,
            beam_size=3,
        )
        text = " ".join(seg.text.strip() for seg in segments)
        return text.strip()
