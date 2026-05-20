# Data Model: Performance Logging

**Branch**: `003-performance-logging` | **Date**: 2026-05-20

## Schema Changes

### New table: `perf_logs`

```sql
CREATE TABLE IF NOT EXISTS perf_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    segment_id INTEGER NOT NULL,
    method TEXT NOT NULL,
    duration_sec REAL NOT NULL,
    audio_duration_sec REAL NOT NULL,
    logged_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_perf_logs_method_time
    ON perf_logs(method, logged_at);

CREATE INDEX IF NOT EXISTS idx_perf_logs_segment
    ON perf_logs(segment_id);
```

### Migration (for existing installs)

The table is created in `schema.sql` (safe via `IF NOT EXISTS`). No `ALTER TABLE`
migration is needed since this is a new table, not a column addition.

## Entity: PerfLog

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | INTEGER | PK, AUTOINCREMENT | Row identifier |
| segment_id | INTEGER | NOT NULL | References `segments.id` (logical FK) |
| method | TEXT | NOT NULL | One of: `"remux"`, `"transcribe"`, `"rotate"` |
| duration_sec | REAL | NOT NULL | Wall-clock time for the operation |
| audio_duration_sec | REAL | NOT NULL | Duration of the audio segment (end_ts - start_ts) |
| logged_at | REAL | NOT NULL | Unix epoch when the log was written |

### Indexes

| Index | Columns | Purpose |
|-------|---------|---------|
| `idx_perf_logs_method_time` | `(method, logged_at)` | Serves the `//perfstats` aggregate query (GROUP BY method, WHERE logged_at >= ?) |
| `idx_perf_logs_segment` | `(segment_id)` | Serves cascade delete when segments are cleaned up |

### Valid `method` values

| Value | Instrumented function | Module |
|-------|----------------------|--------|
| `"remux"` | `VoiceRecorder._remux_pcm_to_ogg` | `recording/recorder.py` |
| `"transcribe"` | `Transcriber._transcribe` | `recording/transcriber.py` |
| `"rotate"` | `VoiceRecorder._rotate_user` | `recording/recorder.py` |

## Relationships

```
segments (1) ──── (N) perf_logs
   id    ◄──────────── segment_id
```

Each segment produces up to 3 perf_logs rows (one per method). Rows are
deleted when the parent segment is cleaned up by the retention system.

## State Transitions

```
segment recorded
    └─► _rotate_user completes  → perf_logs row (method="rotate")
    └─► _remux_worker completes → perf_logs row (method="remux")
    └─► _transcribe completes   → perf_logs row (method="transcribe")

retention cleanup runs
    └─► DELETE FROM perf_logs WHERE segment_id IN (...)
    └─► DELETE FROM segments WHERE id IN (...)
```
