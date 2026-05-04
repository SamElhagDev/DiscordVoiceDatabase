import aiosqlite
import os
import time


class DatabaseManager:
    def __init__(self, *, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    # ── Consent / participation operations ──────────────────────────────

    async def register_user(self, guild_id: int, user_id: int, username: str) -> bool:
        """Opt a user in to recording. Returns True if newly registered, False if already registered."""
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
            return True

    async def unregister_user(self, guild_id: int, user_id: int) -> bool:
        """Opt a user out of recording. Returns True if was registered, False if wasn't."""
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
        cursor = await self.connection.execute(
            "INSERT INTO segments(guild_id, channel_id, user_id, start_ts, file_path) VALUES (?, ?, ?, ?, ?)",
            (guild_id, channel_id, user_id, start_ts, file_path),
        )
        await self.connection.commit()
        return cursor.lastrowid

    async def close_segment(self, segment_id: int, end_ts: float, file_size: int = 0):
        await self.connection.execute(
            "UPDATE segments SET end_ts=?, file_size=? WHERE id=?",
            (end_ts, file_size, segment_id),
        )
        await self.connection.commit()

    async def update_segment_file_size(self, segment_id: int, file_size: int):
        await self.connection.execute(
            "UPDATE segments SET file_size=? WHERE id=?",
            (file_size, segment_id),
        )
        await self.connection.commit()

    async def get_segments_in_range(
        self, user_id: int, start_ts: float, end_ts: float, guild_id: int = None
    ) -> list:
        """Find all segments that overlap the requested time window."""
        if guild_id:
            rows = await self.connection.execute(
                """SELECT id, guild_id, channel_id, user_id, start_ts, end_ts, file_path, file_size
                   FROM segments
                   WHERE user_id=? AND guild_id=?
                     AND start_ts <= ? AND (end_ts >= ? OR end_ts IS NULL)
                   ORDER BY start_ts ASC""",
                (user_id, guild_id, end_ts, start_ts),
            )
        else:
            rows = await self.connection.execute(
                """SELECT id, guild_id, channel_id, user_id, start_ts, end_ts, file_path, file_size
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
        placeholders = ",".join("?" for _ in segment_ids)
        await self.connection.execute(
            f"DELETE FROM segments WHERE id IN ({placeholders})",
            segment_ids,
        )
        await self.connection.commit()

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
        await self.connection.execute(
            """INSERT INTO recording_settings(guild_id, primary_channel_id)
               VALUES (?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET primary_channel_id=excluded.primary_channel_id""",
            (guild_id, channel_id),
        )
        await self.connection.commit()

    async def set_retention_days(self, guild_id: int, days: int):
        await self.connection.execute(
            """INSERT INTO recording_settings(guild_id, retention_days)
               VALUES (?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET retention_days=excluded.retention_days""",
            (guild_id, days),
        )
        await self.connection.commit()
