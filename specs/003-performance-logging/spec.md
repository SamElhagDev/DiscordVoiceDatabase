# Feature Specification: Performance Logging

**Branch**: `003-performance-logging` | **Date**: 2026-05-20

## Overview

Add a `perf_logs` table to the database that records wall-clock completion
times for each processing method that touches a segment. A new `//perfstats`
Discord command displays running averages of processing time per method,
normalised to a 60-second audio segment.

## Clarifications

### Session 2026-05-20

- Q: Should failed operations (e.g., FFmpeg error, transcription crash) be recorded in perf_logs? → A: No — only log timing for successful operations. Failures are already captured in application logs.
- Q: Should perf_logs have an independent retention period or cascade with segment deletion? → A: Cascade with segments — perf data is deleted when the parent segment expires.
- Q: Should `//perfstats` be restricted to admins or available to all users? → A: All users — no permission guard required.

## Requirements

### Functional

1. A new `perf_logs` table stores one row per timed operation:
   - `segment_id` (FK to segments), `method` (text), `duration_sec` (real),
     `audio_duration_sec` (real — the segment's actual audio length),
     `logged_at` (real — Unix epoch).
2. The following methods are instrumented:
   - `remux` — PCM-to-OGG FFmpeg conversion (`recorder.py:_remux_pcm_to_ogg`)
   - `transcribe` — Whisper inference (`transcriber.py:_transcribe`)
   - `rotate` — segment rotation: disk flush + DB insert (`recorder.py:_rotate_user`)
3. Each timing is captured around the actual work, not around queue waits. Timing
   is only recorded on the success path — failed operations are not logged to
   `perf_logs` (they are already captured in the application log).
4. A new `//perfstats` hybrid command displays an embed with:
   - Per-method rows: method name, average processing time (for a 60s segment),
     sample count, and last 24h average.
   - Overall pipeline total (sum of method averages).
5. The command takes an optional `days` parameter (default 7) to control the
   lookback window for the averages.

### Non-Functional

- Perf log writes MUST NOT block the audio path. They run inside the remux
  worker or transcription worker (already in background threads/tasks), so
  the timing write happens after the work completes — one extra async DB call
  per segment per method.
- The `perf_logs` table MUST be cleaned up by the existing retention system
  when the parent segment is deleted.
- No new Python dependencies.
- Schema created via `schema.sql`; migration via `ALTER TABLE` pattern for
  existing installs.
