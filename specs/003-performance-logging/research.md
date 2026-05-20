# Research: Performance Logging

**Branch**: `003-performance-logging` | **Date**: 2026-05-20

## R1: Where to place timers in the pipeline

**Decision**: Instrument three methods, timing only the compute work (not queue waits).

**Rationale**:
| Method | Where work happens | Current code path |
|--------|-------------------|-------------------|
| `remux` | `_remux_pcm_to_ogg` (subprocess) | Called from `_remux_worker` via `asyncio.to_thread` |
| `transcribe` | `_transcribe` (Whisper inference) | Called from `Transcriber._worker` via `asyncio.to_thread` |
| `rotate` | `_rotate_user` (flush + DB write) | Called from `_rotation_loop` directly |

The timer wraps the actual function call, so queue wait time (sitting in
`asyncio.Queue`) is excluded — that would skew the averages toward idle time
rather than compute cost.

**Alternatives considered**:
- *Instrument every DB method*: Too fine-grained; most DB calls are <1ms and
  not actionable. The three pipeline stages are where time is actually spent.
- *Log to file instead of DB*: Makes the `//perfstats` command much harder —
  would need log parsing. DB storage enables simple `SELECT AVG(...)` queries.

## R2: Normalising to "per 60s segment"

**Decision**: Store raw `duration_sec` and `audio_duration_sec` separately;
compute the normalised rate at query time.

**Rationale**: Segments are configurable (`segment_duration_sec` in settings,
default 60). If the operator changes segment duration, stored normalised
values would be incorrect. Computing `AVG(duration_sec * 60.0 / audio_duration_sec)`
at query time handles mixed durations correctly.

Formula: `normalised = duration_sec * (60.0 / audio_duration_sec)`

**Alternatives considered**:
- *Store pre-normalised values*: Simpler queries but breaks if segment
  duration changes.

## R3: Cleanup strategy for perf_logs

**Decision**: Delete `perf_logs` rows when the parent segment is deleted by
the retention system.

**Rationale**: `SegmentCleanup.run_cleanup` calls
`db.delete_segments_by_ids(ids)`. Adding a cascading delete in the same
method (delete from perf_logs WHERE segment_id IN (...)) keeps the table
bounded. A foreign key with `ON DELETE CASCADE` would also work but the
project's migration pattern uses manual SQL.

**Alternatives considered**:
- *Time-based TTL independent of segments*: Simpler, but could leave orphaned
  rows if retention periods differ.
- *Foreign key CASCADE*: Would require `PRAGMA foreign_keys = ON` which the
  project doesn't currently enable. Not worth the change.

## R4: Query for running averages

**Decision**: Single aggregate query grouped by method, with time window filter.

```sql
SELECT
    method,
    COUNT(*) AS sample_count,
    AVG(duration_sec * 60.0 / audio_duration_sec) AS avg_per_60s,
    AVG(CASE WHEN logged_at >= ? THEN duration_sec * 60.0 / audio_duration_sec END) AS avg_24h
FROM perf_logs
WHERE logged_at >= ?
GROUP BY method
ORDER BY method
```

The two `?` params are: (1) Unix epoch for 24h ago (inner CASE), (2) Unix
epoch for N days ago (outer WHERE). This gives both the full-window and
last-24h averages in one query.

**Alternatives considered**:
- *Separate queries per method*: More round-trips for no benefit.
- *Materialised view*: Over-engineered for a low-volume table.

## R5: Constitution compliance

**Decision**: No principles violated.

- **Principle I (Consent)**: No recording changes.
- **Principle II (Data Integrity)**: `perf_logs` is a new auxiliary table.
  Segment data integrity is unchanged. Perf rows are cleaned up with their
  parent segments.
- **Principle III (Accurate Playback)**: No audio path changes.
- **Principle IV (Non-Blocking)**: Timer wraps are around code that already
  runs in background workers. The one additional `db.log_perf` call per
  timed operation is async and serialised by the existing DB lock — it
  cannot block the audio receive path.
- **Principle V (User-Facing Clarity)**: The `//perfstats` command presents
  results in a clear embed with labelled fields.
