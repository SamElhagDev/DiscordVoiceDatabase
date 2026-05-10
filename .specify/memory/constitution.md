<!--
  Sync Impact Report
  ─────────────────────────────────────────────
  Version change: N/A (initial) → 1.0.0
  Modified principles: N/A (initial ratification)
  Added sections:
    - Core Principles (I–V)
    - Operational Constraints
    - Development Workflow
    - Governance
  Removed sections: None
  Templates requiring updates:
    - .specify/templates/plan-template.md — ✅ reviewed, no changes needed
    - .specify/templates/spec-template.md — ✅ reviewed, no changes needed
    - .specify/templates/tasks-template.md — ✅ reviewed, no changes needed
  Follow-up TODOs: None
-->

# Discord Voice Database Constitution

## Core Principles

### I. Consent-First Recording

All voice recording MUST be opt-in on a per-user, per-guild basis.
No audio data is captured for any user who has not explicitly granted
consent via the `/join` command. Consent state MUST be checked on
every incoming audio packet, not cached beyond a single session
refresh cycle. Users MUST be able to revoke consent at any time via
`/leave`, and revocation MUST take effect immediately for all future
packets.

**Rationale**: Recording people without consent is both unethical and
illegal in many jurisdictions. This is the foundational trust
contract of the entire system.

### II. Data Integrity & Retention

Recorded audio segments MUST be indexed in the database with accurate
timestamps, file paths, and transcription state. Segments MUST NOT be
silently dropped — if a segment is recorded, it MUST appear in the DB
and be retrievable until the retention policy deletes it. The retention
cleanup process MUST delete both the file on disk and the
corresponding database row atomically. Per-guild retention settings
MUST be honored over global defaults.

**Rationale**: The core value proposition is a reliable record of what
was said. Orphaned files, phantom DB rows, or premature deletion
undermine that completely.

### III. Accurate Playback

When a user selects a segment and requests playback or download, the
system MUST deliver audio that corresponds to the selected segment and
requested time range. Trim offsets MUST be computed relative to the
selected segment, never relative to earlier segments returned by
overlap queries. If the requested range produces no usable audio, the
system MUST report a clear error rather than playing silence.

**Rationale**: Playing the wrong clip or silent audio with no error
erodes user trust and makes the tool unreliable for its primary
purpose.

### IV. Non-Blocking Architecture

Audio packet handling (the voice receive callback) MUST never perform
blocking I/O or await async operations. Transcription, remuxing, and
cleanup MUST run in background workers or threads and never block the
Discord event loop. If a background task fails, it MUST log the error
and continue processing the queue — a single failed segment MUST NOT
halt the pipeline.

**Rationale**: The bot sits on Discord's real-time voice stream. Any
blocking in the audio path causes dropped packets, which means lost
audio — the one thing this system cannot afford.

### V. User-Facing Clarity

Every user-facing command MUST provide clear feedback: success,
failure with reason, or progress for long-running operations.
Discord UI constraints (embed field limits, select menu limits,
timestamp rendering quirks) MUST be respected in code, not discovered
at runtime. Timestamps shown to users MUST use the configured display
timezone and 12-hour format consistently.

**Rationale**: The bot serves non-technical Discord users. Cryptic
errors, raw timestamps, or silent failures make the tool unusable in
practice.

## Operational Constraints

- **Storage format**: OGG/Opus via FFmpeg. Raw PCM is an intermediate
  format and MUST be deleted after successful remux.
- **Database**: SQLite via aiosqlite. All mutation operations MUST be
  serialized to avoid interleaved commit corruption.
- **Transcription**: faster-whisper running on CPU. Model loading MUST
  be lazy (first use) to avoid startup RAM overhead. Empty
  transcriptions MUST be stored as "Blank" and filtered from user-facing
  listings.
- **Voice encryption**: DAVE E2EE MUST be handled transparently.
  Decryption failures during transition windows MUST fall back to
  treating packets as unencrypted rather than dropping them.
- **Deployment**: Self-hosted Windows runner via GitHub Actions.
  The bot runs as a Windows Scheduled Task under SYSTEM. All paths
  MUST be configurable via environment variables.
- **Dependencies**: All Python dependencies MUST be declared in
  `requirements.txt`. Platform-specific packages (e.g., `tzdata` for
  Windows timezone support) MUST be included.

## Development Workflow

- **Logging**: All state mutations, command invocations, and error
  conditions MUST be logged. Use INFO for state changes visible to
  users, DEBUG for internal pipeline events, WARNING for recoverable
  anomalies, ERROR for failures.
- **Schema migrations**: Applied idempotently via `ALTER TABLE` wrapped
  in try/except in `bot.py:init_db()`. New columns MUST have safe
  defaults.
- **Clip retrieval**: When a user selects a segment from the UI, the
  retriever MUST use the selected segment as the anchor and query
  forward only. The `anchor_seg` and `offset_sec` parameters MUST be
  passed from the UI modals.
- **Error recovery**: Opus decoders MUST be reset on corruption.
  FFmpeg failures MUST be logged with stderr output. File-not-found
  conditions MUST report which specific file is missing.

## Governance

This constitution defines the non-negotiable behavioral contracts of
the Discord Voice Database system. All changes to recording, playback,
consent, or data retention logic MUST be evaluated against these
principles before implementation.

**Amendment procedure**: Any change to this document MUST include:
1. A description of what is changing and why.
2. An updated version number following semantic versioning:
   - MAJOR: Principle removed or redefined incompatibly.
   - MINOR: New principle or section added.
   - PATCH: Clarification or wording fix.
3. Updated `LAST_AMENDED_DATE`.

**Compliance**: Code reviews SHOULD verify that new features and bug
fixes do not violate constitution principles. The Sync Impact Report
at the top of this file MUST be updated on every amendment.

**Version**: 1.0.0 | **Ratified**: 2026-05-09 | **Last Amended**: 2026-05-09
