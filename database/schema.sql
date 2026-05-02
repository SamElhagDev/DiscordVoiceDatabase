CREATE TABLE IF NOT EXISTS `consent` (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    username TEXT,
    granted INTEGER NOT NULL DEFAULT 1,
    opted_in_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS `segments` (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    start_ts REAL NOT NULL,
    end_ts REAL,
    file_path TEXT NOT NULL,
    file_size INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_segments_user_ts ON segments(user_id, start_ts, end_ts);
CREATE INDEX IF NOT EXISTS idx_segments_guild_ts ON segments(guild_id, start_ts, end_ts);

CREATE TABLE IF NOT EXISTS `recording_settings` (
    guild_id INTEGER PRIMARY KEY,
    primary_channel_id INTEGER,
    segment_duration_sec INTEGER NOT NULL DEFAULT 60,
    retention_days INTEGER NOT NULL DEFAULT 7,
    enabled INTEGER NOT NULL DEFAULT 1
);

