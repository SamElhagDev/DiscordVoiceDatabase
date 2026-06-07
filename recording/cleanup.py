"""
Segment cleanup — periodically prunes old recordings based on retention policy.
"""

import asyncio
import logging
import os

logger = logging.getLogger("discord_bot")


RECORDINGS_PATH = os.path.abspath(os.getenv("DiscordVoiceDatabase_RECORDINGS_PATH", "recordings"))


class SegmentCleanup:
    """Runs periodic cleanup of old audio segments."""

    def __init__(self, database, default_retention_days: int = 7):
        self.db = database
        self.default_retention_days = default_retention_days
        self._task: asyncio.Task = None

    def start(self):
        self._task = asyncio.create_task(self._cleanup_loop())
        logger.info(f"Segment cleanup started (retention={self.default_retention_days} days)")

    def stop(self):
        if self._task:
            self._task.cancel()
            logger.info("Segment cleanup stopped")

    async def _cleanup_loop(self):
        """Run cleanup every hour."""
        try:
            while True:
                logger.debug("Running scheduled cleanup cycle...")
                await self._run_cleanup()
                await asyncio.sleep(3600)  # every hour
        except asyncio.CancelledError:
            logger.debug("Cleanup loop cancelled")

    async def _run_cleanup(self):
        try:
            old_segments = await self.db.get_expired_segments_per_guild(self.default_retention_days)
            if not old_segments:
                return

            removed = 0
            deleted_ids = []
            recordings_root = os.path.abspath(RECORDINGS_PATH)

            for seg_id, file_path in old_segments:
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    pcm_path = os.path.splitext(file_path)[0] + ".pcm"
                    if os.path.exists(pcm_path):
                        os.remove(pcm_path)
                    removed += 1
                    deleted_ids.append(seg_id)

                    parent = os.path.dirname(file_path)
                    while parent and os.path.abspath(parent) != recordings_root:
                        try:
                            os.rmdir(parent)
                            parent = os.path.dirname(parent)
                        except OSError:
                            break
                except OSError as e:
                    logger.warning(f"Failed to delete {file_path}: {e}")

            if deleted_ids:
                await self.db.delete_perf_logs_by_segment_ids(deleted_ids)
                await self.db.delete_segments_by_ids(deleted_ids)

            if removed > 0:
                logger.info(f"Cleanup: removed {removed} old segment files")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
