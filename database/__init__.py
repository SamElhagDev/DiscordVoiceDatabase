import asyncio

import aiosqlite
import logging
import os
import time

logger = logging.getLogger("discord_bot")


class DatabaseManager:
    def __init__(self, *, connection: aiosqlite.Connection) -> None:
        self.connection = connection
        self._lock = asyncio.Lock()

    # ── Consent / participation operations ──────────────────────────────

    async def register_user(self, guild_id: int, user_id: int, username: str) -> bool:
        """Opt a user in to recording. Returns True if newly registered, False if already registered."""
        async with self._lock:
            existing = await self.connection.execute(
                "SELECT granted FROM consent WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            )
            async with existing as cursor:
                row = await cursor.fetchone()
                if row is not None:
                    if row[0] == 1:
                        return False  # already registered
                    await self.connection.execute(
                        "UPDATE consent SET granted=1, username=?, opted_in_at=CURRENT_TIMESTAMP WHERE guild_id=? AND user_id=?",
                        (username, guild_id, user_id),
                    )
                else:
                    await self.connection.execute(
                        "INSERT INTO consent(guild_id, user_id, username, granted) VALUES (?, ?, ?, 1)",
                        (guild_id, user_id, username),
                    )
                await self.connection.commit()
                logger.info(f"User registered: guild={guild_id} user={user_id} ({username})")
                return True

    async def unregister_user(self, guild_id: int, user_id: int) -> bool:
        """Opt a user out of recording. Returns True if was registered, False if wasn't."""
        async with self._lock:
            existing = await self.connection.execute(
                "SELECT granted FROM consent WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            )
            async with existing as cursor:
                row = await cursor.fetchone()
                if row is None or row[0] == 0:
                    return False
                await self.connection.execute(
                    "UPDATE consent SET granted=0 WHERE guild_id=? AND user_id=?",
                    (guild_id, user_id),
                )
                await self.connection.commit()
                logger.info(f"User unregistered: guild={guild_id} user={user_id}")
                return True

    async def is_user_registered(self, guild_id: int, user_id: int) -> bool:
        rows = await self.connection.execute(
            "SELECT granted FROM consent WHERE guild_id=? AND user_id=? AND granted=1",
            (guild_id, user_id),
        )
        async with rows as cursor:
            return await cursor.fetchone() is not None

    async def get_participants(self, guild_id: int) -> list:
        """Get all opted-in users for a guild."""
        rows = await self.connection.execute(
            "SELECT user_id, username, opted_in_at FROM consent WHERE guild_id=? AND granted=1 ORDER BY opted_in_at ASC",
            (guild_id,),
        )
        async with rows as cursor:
            return await cursor.fetchall()

    async def get_consented_user_ids(self, guild_id: int) -> set:
        """Return a set of user_ids that have consented in this guild."""
        rows = await self.connection.execute(
            "SELECT user_id FROM consent WHERE guild_id=? AND granted=1",
            (guild_id,),
        )
        async with rows as cursor:
            result = await cursor.fetchall()
            return {row[0] for row in result}

    # ── Segment operations ──────────────────────────────────────────────

    async def add_segment(
        self, guild_id: int, channel_id: int, user_id: int, start_ts: float, file_path: str
    ) -> int:
        async with self._lock:
            cursor = await self.connection.execute(
                "INSERT INTO segments(guild_id, channel_id, user_id, start_ts, file_path) VALUES (?, ?, ?, ?, ?)",
                (guild_id, channel_id, user_id, start_ts, file_path),
            )
            await self.connection.commit()
            logger.debug(f"Segment added: id={cursor.lastrowid} user={user_id} file={file_path}")
            return cursor.lastrowid

    async def close_segment(self, segment_id: int, end_ts: float, file_size: int = 0):
        async with self._lock:
            await self.connection.execute(
                "UPDATE segments SET end_ts=?, file_size=? WHERE id=?",
                (end_ts, file_size, segment_id),
            )
            await self.connection.commit()
            logger.debug(f"Segment closed: id={segment_id} size={file_size}")

    async def update_segment_file_size(self, segment_id: int, file_size: int):
        async with self._lock:
            await self.connection.execute(
                "UPDATE segments SET file_size=? WHERE id=?",
                (file_size, segment_id),
            )
            await self.connection.commit()

    async def get_segments_in_range(
        self, user_id: int, start_ts: float, end_ts: float, guild_id: int = None
    ) -> list:
        """Find all segments that overlap the requested time window.
        Returns tuples: (id, guild_id, channel_id, user_id, start_ts, end_ts, file_path, file_size, transcript)
        """
        if guild_id:
            rows = await self.connection.execute(
                """SELECT id, guild_id, channel_id, user_id, start_ts, end_ts, file_path, file_size, transcript
                   FROM segments
                   WHERE user_id=? AND guild_id=?
                     AND start_ts <= ? AND (end_ts >= ? OR end_ts IS NULL)
                   ORDER BY start_ts ASC""",
                (user_id, guild_id, end_ts, start_ts),
            )
        else:
            rows = await self.connection.execute(
                """SELECT id, guild_id, channel_id, user_id, start_ts, end_ts, file_path, file_size, transcript
                   FROM segments
                   WHERE user_id=? AND start_ts <= ? AND (end_ts >= ? OR end_ts IS NULL)
                   ORDER BY start_ts ASC""",
                (user_id, end_ts, start_ts),
            )
        async with rows as cursor:
            return await cursor.fetchall()

    async def get_expired_segments(self, retention_days: int) -> list:
        cutoff = time.time() - (retention_days * 86400)
        rows = await self.connection.execute(
            "SELECT id, file_path FROM segments WHERE end_ts IS NOT NULL AND end_ts < ?",
            (cutoff,),
        )
        async with rows as cursor:
            return await cursor.fetchall()

    async def delete_segments_by_ids(self, segment_ids: list):
        if not segment_ids:
            return
        async with self._lock:
            placeholders = ",".join("?" for _ in segment_ids)
            await self.connection.execute(
                f"DELETE FROM segments WHERE id IN ({placeholders})",
                segment_ids,
            )
            await self.connection.commit()
            logger.info(f"Deleted {len(segment_ids)} segment(s) from DB")

    # ── Settings operations ─────────────────────────────────────────────

    async def get_settings(self, guild_id: int) -> dict:
        rows = await self.connection.execute(
            "SELECT primary_channel_id, segment_duration_sec, retention_days, enabled FROM recording_settings WHERE guild_id=?",
            (guild_id,),
        )
        async with rows as cursor:
            row = await cursor.fetchone()
            if row is None:
                return {
                    "primary_channel_id": None,
                    "segment_duration_sec": 60,
                    "retention_days": 7,
                    "enabled": True,
                }
            return {
                "primary_channel_id": row[0],
                "segment_duration_sec": row[1],
                "retention_days": row[2],
                "enabled": bool(row[3]),
            }

    async def set_primary_channel(self, guild_id: int, channel_id: int):
        async with self._lock:
            await self.connection.execute(
                """INSERT INTO recording_settings(guild_id, primary_channel_id)
                   VALUES (?, ?)
                   ON CONFLICT(guild_id) DO UPDATE SET primary_channel_id=excluded.primary_channel_id""",
                (guild_id, channel_id),
            )
            await self.connection.commit()
            logger.info(f"Primary channel set: guild={guild_id} channel={channel_id}")

    async def set_retention_days(self, guild_id: int, days: int):
        async with self._lock:
            await self.connection.execute(
                """INSERT INTO recording_settings(guild_id, retention_days)
                   VALUES (?, ?)
                   ON CONFLICT(guild_id) DO UPDATE SET retention_days=excluded.retention_days""",
                (guild_id, days),
            )
            await self.connection.commit()
            logger.info(f"Retention set: guild={guild_id} days={days}")

    # ── Transcription operations ───────────────────────────────────────

    async def set_segment_transcript(self, segment_id: int, transcript: str):
        async with self._lock:
            await self.connection.execute(
                "UPDATE segments SET transcript=? WHERE id=?",
                (transcript, segment_id),
            )
            await self.connection.commit()
            logger.debug(f"Transcript saved: segment={segment_id} length={len(transcript)}")

    async def get_segment_transcript(self, segment_id: int) -> str | None:
        rows = await self.connection.execute(
            "SELECT transcript FROM segments WHERE id=?",
            (segment_id,),
        )
        async with rows as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def search_segments_by_transcript(
        self, user_id: int, start_ts: float, end_ts: float, query: str, guild_id: int = None
    ) -> list:
        """Search segments whose transcript contains the query text.
        Returns same tuple shape as get_segments_in_range.
        """
        like_param = f"%{query}%"
        if guild_id:
            rows = await self.connection.execute(
                """SELECT id, guild_id, channel_id, user_id, start_ts, end_ts, file_path, file_size, transcript
                   FROM segments
                   WHERE user_id=? AND guild_id=?
                     AND start_ts <= ? AND (end_ts >= ? OR end_ts IS NULL)
                     AND transcript IS NOT NULL AND transcript != '' AND transcript != 'Blank'
                     AND transcript LIKE ? COLLATE NOCASE
                   ORDER BY start_ts ASC""",
                (user_id, guild_id, end_ts, start_ts, like_param),
            )
        else:
            rows = await self.connection.execute(
                """SELECT id, guild_id, channel_id, user_id, start_ts, end_ts, file_path, file_size, transcript
                   FROM segments
                   WHERE user_id=? AND start_ts <= ? AND (end_ts >= ? OR end_ts IS NULL)
                     AND transcript IS NOT NULL AND transcript != '' AND transcript != 'Blank'
                     AND transcript LIKE ? COLLATE NOCASE
                   ORDER BY start_ts ASC""",
                (user_id, end_ts, start_ts, like_param),
            )
        async with rows as cursor:
            return await cursor.fetchall()

    async def get_talk_time_by_user(self, guild_id: int, since_ts: float = 0.0) -> list:
        """Return total spoken seconds per user for a guild since since_ts.
        Returns list of (user_id, total_seconds) ordered by total_seconds DESC.
        Only counts completed segments (end_ts IS NOT NULL).
        """
        rows = await self.connection.execute(
            """SELECT user_id, SUM(end_ts - start_ts) as total_seconds
               FROM segments
               WHERE guild_id=? AND end_ts IS NOT NULL AND start_ts >= ?
               GROUP BY user_id
               ORDER BY total_seconds DESC""",
            (guild_id, since_ts),
        )
        async with rows as cursor:
            return await cursor.fetchall()

    async def get_untranscribed_segments(self, guild_id: int = None) -> list:
        """Return all completed segments that have no transcript yet."""
        if guild_id:
            rows = await self.connection.execute(
                """SELECT id, file_path FROM segments
                   WHERE end_ts IS NOT NULL AND (transcript IS NULL OR transcript = '')
                   AND guild_id=?
                   ORDER BY start_ts ASC""",
                (guild_id,),
            )
        else:
            rows = await self.connection.execute(
                """SELECT id, file_path FROM segments
                   WHERE end_ts IS NOT NULL AND (transcript IS NULL OR transcript = '')
                   ORDER BY start_ts ASC""",
            )
        async with rows as cursor:
            return await cursor.fetchall()
