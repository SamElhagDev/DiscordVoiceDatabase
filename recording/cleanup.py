"""
Segment cleanup — periodically prunes old recordings based on retention policy.
"""

import asyncio
import logging
import os

logger = logging.getLogger("discord_bot")


class SegmentCleanup:
    """Runs periodic cleanup of old audio segments."""

    def __init__(self, database, default_retention_days: int = 7):
        self.db = database
        self.default_retention_days = default_retention_days
        self._task: asyncio.Task = None

    def start(self):
        self._task = asyncio.create_task(self._cleanup_loop())

    def stop(self):
        if self._task:
            self._task.cancel()

    async def _cleanup_loop(self):
        """Run cleanup every hour."""
        try:
            while True:
                await self._run_cleanup()
                await asyncio.sleep(3600)  # every hour
        except asyncio.CancelledError:
            pass

    async def _run_cleanup(self):
        try:
            old_files = await self.db.prune_old_segments(self.default_retention_days)
            removed = 0
            for file_path in old_files:
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        removed += 1
                        # Try to remove empty parent directories
                        parent = os.path.dirname(file_path)
                        while parent and parent != "recordings":
                            try:
                                os.rmdir(parent)  # only removes if empty
                                parent = os.path.dirname(parent)
                            except OSError:
                                break
                except OSError as e:
                    logger.warning(f"Failed to delete {file_path}: {e}")

            if removed > 0:
                logger.info(f"Cleanup: removed {removed} old segment files")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
