# Implementation Plan: `//searchtext` Command

**Branch**: `main` | **Date**: 2026-05-20 | **Spec**: [spec.md](specs/004-add-searchtext-command/spec.md)
**Input**: Feature specification from `specs/004-add-searchtext-command/spec.md`

## Summary

Add a `//searchtext` hybrid command that reuses the existing transcript search logic from `//search` (`search_segments_by_transcript`), but displays results using `mode="text"` in `_ClipSelectView` — posting the selected segment's transcript as a plain text message in the channel instead of opening an audio playback modal. This requires adding one new command method; all changes are confined to `cogs/voicedatabase.py`.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: discord.py (with voice_recv extension), aiosqlite
**Storage**: SQLite via aiosqlite (read-only for this feature)
**Testing**: Manual (bot testing in Discord)
**Target Platform**: Windows 11 (self-hosted via GitHub Actions)
**Project Type**: Discord bot (cog-based architecture)
**Performance Goals**: N/A — lightweight text display
**Constraints**: Discord message limit of 2000 chars (handled by existing chunking in `_ClipSelectView.on_select`); select menu limit of 25 options (handled by existing pagination)
**Scale/Scope**: Single-file change, ~40 lines of new code

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Consent-First Recording | PASS | Read-only — no recording occurs |
| II. Data Integrity & Retention | PASS | No mutations to segments table |
| III. Accurate Playback | N/A | No audio involved; transcript displayed is the one stored for the selected segment |
| IV. Non-Blocking Architecture | PASS | No file I/O or audio processing; database query is async |
| V. User-Facing Clarity | PASS | Same UI patterns as `//search` and `//listtext`. Transcript includes user attribution and timestamp. Missing transcripts handled with explicit ephemeral message. |

**Post-Design Re-Check**: All principles still pass. The `mode="text"` path in `on_select` uses `interaction.response.send_message` (async, non-blocking) and displays transcript from the already-loaded segment tuple (no additional queries).

## Project Structure

### Documentation (this feature)

```text
specs/004-add-searchtext-command/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Research findings
├── data-model.md        # Data model (no schema changes)
├── quickstart.md        # Implementation quickstart guide
└── contracts/
    └── command-schema.md  # Command contract
```

### Source Code (repository root)

```text
cogs/
└── voicedatabase.py     # ONLY file modified — new command method
```

**Structure Decision**: Single-file change within the existing cog. No new files, modules, or directories needed.

## Complexity Tracking

No constitution violations. No complexity escalation needed.

## Implementation Details

### Change 1: Add `searchtext` command method

**File**: `cogs/voicedatabase.py`
**Location**: After the existing `search_clips` method (line ~671)

Add a new `@commands.hybrid_command` method `search_text` that:

1. Uses the same decorator pattern as `search_clips`:
   - `name="searchtext"`
   - Same `@app_commands.describe` for `user`, `date`, `query`, `end_date`
2. Copies the body of `search_clips` exactly, with two differences:
   - Passes `mode="text"` to `_ClipSelectView` constructor
   - Updates the `logger.info` message to say `/searchtext` instead of `/search`

No changes to `_ClipSelectView`, database methods, or any other component.

### Why not refactor `search_clips` to accept a mode parameter?

The codebase follows a pattern of explicit separate methods per command (see `list_clips` vs `list_text`). The duplication is minimal (~30 lines), and separate methods allow the two commands to diverge independently in the future without risk of breaking the other.
