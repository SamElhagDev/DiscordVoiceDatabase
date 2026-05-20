# Tasks: Performance Logging

**Input**: Design documents from `specs/003-performance-logging/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/command-schema.md, quickstart.md

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1 = pipeline instrumentation, US2 = stats command)
- Exact file paths included in every task description

---

## Phase 1: Setup (Schema)

**Purpose**: Create the `perf_logs` table so all subsequent tasks can write to it

- [x] T001 Add `perf_logs` table and indexes to database/schema.sql — append `CREATE TABLE IF NOT EXISTS perf_logs` with columns `(id INTEGER PRIMARY KEY AUTOINCREMENT, segment_id INTEGER NOT NULL, method TEXT NOT NULL, duration_sec REAL NOT NULL, audio_duration_sec REAL NOT NULL, logged_at REAL NOT NULL)` plus `idx_perf_logs_method_time (method, logged_at)` and `idx_perf_logs_segment (segment_id)` indexes, all with `IF NOT EXISTS`

---

## Phase 2: Foundational (Database Methods)

**Purpose**: Add all DB methods that both user stories depend on — MUST complete before any instrumentation or command work

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T002 Add `log_perf(segment_id, method, duration_sec, audio_duration_sec)` method to database/__init__.py — INSERT into `perf_logs` with `time.time()` for `logged_at`, using the existing `self._lock` pattern
- [x] T003 Add `get_perf_stats(since_ts, since_24h_ts)` method to database/__init__.py — SELECT grouped by method returning `(method, count, avg_per_60s, avg_24h)` using formula `AVG(duration_sec * 60.0 / audio_duration_sec)` with a CASE for the 24h window; filter `audio_duration_sec > 0`
- [x] T004 Add `delete_perf_logs_by_segment_ids(segment_ids)` method to database/__init__.py — bulk DELETE from `perf_logs WHERE segment_id IN (...)` using the existing `self._lock` and placeholder pattern from `delete_segments_by_ids`

**Checkpoint**: Database layer complete — instrumentation and command can now proceed

---

## Phase 3: User Story 1 — Pipeline Instrumentation (Priority: P1) MVP

**Goal**: Instrument the three pipeline stages (rotate, remux, transcribe) to write timing data to `perf_logs` after each segment is processed.

**Independent Test**: Record a voice segment, wait for pipeline completion, then query `SELECT * FROM perf_logs` — should see 3 rows (rotate, remux, transcribe) with reasonable `duration_sec` values.

### Implementation for User Story 1

- [x] T005 [P] [US1] Extend `Transcriber.enqueue` to accept `audio_duration: float` parameter and add timer around `_transcribe` call in recording/transcriber.py — change queue item from `(ogg_path, segment_id)` to `(ogg_path, segment_id, audio_duration)`, wrap `asyncio.to_thread(self._transcribe, ogg_path)` with `time.time()` calls, call `self.db.log_perf(segment_id, "transcribe", duration, audio_duration)` after successful transcription
- [x] T006 [P] [US1] Add timer to `_rotate_user` and extend `_remux_queue` item in recording/recorder.py — wrap the work from `flush_to_disk` through `close_segment` with `time.time()`, compute `audio_dur = end_ts - start_ts`, call `self.db.log_perf(segment_id, "rotate", rotate_dur, audio_dur)`, change queue item from `(pcm_path, ogg_path, segment_id)` to `(pcm_path, ogg_path, segment_id, audio_dur)`
- [x] T007 [US1] Add timer to `_remux_worker` and pass `audio_dur` to transcriber in recording/recorder.py — unpack new queue tuple `(pcm_path, ogg_path, segment_id, audio_dur)`, wrap `asyncio.to_thread(self._remux_pcm_to_ogg, ...)` with `time.time()`, call `self.db.log_perf(segment_id, "remux", remux_dur, audio_dur)`, update `transcriber.enqueue(ogg_path, segment_id, audio_dur)` call to pass `audio_dur`

**Checkpoint**: Pipeline instrumentation complete — all three methods write perf data after each segment

---

## Phase 4: User Story 2 — Stats Command (Priority: P1)

**Goal**: Add `//perfstats` hybrid command that displays running averages per method, normalised to 60s segments.

**Independent Test**: Run `//perfstats` in Discord after recording a few segments — embed shows per-method averages with sample counts and 24h averages. Run with `//perfstats days:1` to verify the lookback window works. Run before any data exists to verify the empty-state message.

### Implementation for User Story 2

- [x] T008 [US2] Add `//perfstats` hybrid command in cogs/voicedatabase.py — add `import time` if missing, create `perf_stats` method with `@commands.hybrid_command(name="perfstats")` and `@app_commands.describe(days="Lookback window in days (default: 7)")`, clamp `days = max(days, 1)`, call `self.bot.database.get_perf_stats(since_ts, since_24h)`, build embed per contracts/command-schema.md (title "Performance Stats", color 0x3498DB, per-method fields with avg/count/24h, footer with pipeline total), handle empty data with ephemeral "No performance data recorded yet." message

**Checkpoint**: Command is functional — displays real data if US1 is also complete, or "no data" if run standalone

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Retention cascade and validation

- [x] T009 Add retention cascade delete to recording/cleanup.py — in `run_cleanup`, call `await self.db.delete_perf_logs_by_segment_ids(ids)` before the existing `await self.db.delete_segments_by_ids(ids)` to ensure perf rows are removed when their parent segments expire
- [x] T010 Manual validation — record a segment, verify 3 perf_log rows appear, run `//perfstats` to check output, then wait for retention cleanup (or trigger manually) and verify perf_log rows are deleted with their segments

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (table must exist before methods reference it)
- **US1 (Phase 3)**: Depends on Phase 2 (needs `log_perf` method)
- **US2 (Phase 4)**: Depends on Phase 2 (needs `get_perf_stats` method) — can run in parallel with US1
- **Polish (Phase 5)**: Depends on Phase 2 (needs `delete_perf_logs_by_segment_ids`)

### User Story Dependencies

- **US1 (Pipeline Instrumentation)**: Can start after Phase 2 — no dependency on US2
- **US2 (Stats Command)**: Can start after Phase 2 — no dependency on US1 (will show "no data" until US1 is also live)

### Within User Story 1

- T005 (transcriber.py) and T006 (recorder.py: rotate) can run in **parallel** — different files
- T007 (recorder.py: remux) depends on **both** T005 (new enqueue signature) and T006 (new queue tuple)

### Parallel Opportunities

```
Phase 1: T001
    ↓
Phase 2: T002 → T003 → T004  (same file, sequential)
    ↓
Phase 3+4:  ┌─ T005 (transcriber.py) ─┐     T008 (voicedatabase.py)
            ├─ T006 (recorder.py)     ─┤       ↑ (parallel with US1)
            └─────────────────────────→ T007
    ↓
Phase 5: T009, T010
```

---

## Parallel Example: User Story 1

```
# After Phase 2 completes, launch T005 and T006 in parallel (different files):
Task T005: "Extend Transcriber.enqueue and add timer in recording/transcriber.py"
Task T006: "Add timer to _rotate_user and extend _remux_queue in recording/recorder.py"

# After both complete, run T007 (same file as T006, depends on T005's new signature):
Task T007: "Add timer to _remux_worker and pass audio_dur to transcriber in recording/recorder.py"
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1: Schema (T001)
2. Complete Phase 2: DB methods (T002–T004)
3. Complete Phase 3: Pipeline instrumentation (T005–T007)
4. **STOP and VALIDATE**: Record a segment, query `perf_logs` table directly
5. Data is flowing — command can be added next

### Full Delivery

1. Phase 1 + 2 → Foundation ready
2. Phase 3 (US1) + Phase 4 (US2) in parallel → Both stories functional
3. Phase 5 → Retention cleanup cascade, manual validation
4. Deploy

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps to US1 (instrumentation) or US2 (command)
- No test tasks included — spec does not request automated tests; validation is manual
- All DB methods use the existing `self._lock` serialisation pattern
- Timer wraps use `time.time()` (wall-clock), not `time.perf_counter()` — consistent with existing timestamp usage in the codebase
- Commit after each task or logical group
