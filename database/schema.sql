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

CREATE TABLE IF NOT EXISTS `perf_logs` (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    segment_id INTEGER NOT NULL,
    method TEXT NOT NULL,
    duration_sec REAL NOT NULL,
    audio_duration_sec REAL NOT NULL,
    logged_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_perf_logs_method_time ON perf_logs(method, logged_at);
CREATE INDEX IF NOT EXISTS idx_perf_logs_segment ON perf_logs(segment_id);

CREATE TABLE IF NOT EXISTS `favorites` (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,          -- Discord ID of the favorite's owner
    guild_id INTEGER NOT NULL,
    segment_id INTEGER NOT NULL,       -- anchor segment (segments.id)
    target_user_id INTEGER NOT NULL,   -- whose audio the clip is of
    offset_sec INTEGER NOT NULL DEFAULT 0,
    duration_min INTEGER NOT NULL DEFAULT 1,
    label TEXT,                        -- cached display string
    created_at REAL NOT NULL,
    UNIQUE(user_id, segment_id, offset_sec, duration_min)
);

CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id, guild_id, created_at);

