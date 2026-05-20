# Tasks: `//listtext` Command

**Input**: Design documents from `specs/001-add-listtext-command/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: No setup needed — all infrastructure exists

*(No tasks — existing project, existing cog, existing database schema)*

---

## Phase 2: Foundational

**Purpose**: No foundational work needed — all prerequisites exist (segment query, `_ClipSelectView`, transcript column)

*(No tasks — reuses existing components entirely)*

---

## Phase 3: User Story 1 — Add `//listtext` command (Priority: P1)

**Goal**: Users can browse segments and view transcript text posted as a plain message in the channel

**Independent Test**: Run `//listtext @user 2026-05-19` in Discord, select a segment from the dropdown, and verify the transcript appears as a plain text message (not an embed) in the channel. Verify other bots can read `message.content`.

### Implementation for User Story 1

- [x] T001 [P] [US1] Add `mode="text"` placeholder and footer text in `_ClipSelectView._rebuild_items` and `build_embed` in cogs/voicedatabase.py
- [x] T002 [US1] Add `mode="text"` handling in `_ClipSelectView.on_select` to post transcript as plain text (with 2000-char message splitting) in cogs/voicedatabase.py
- [x] T003 [US1] Add `listtext` hybrid command method (mirrors `list_clips`, passes `mode="text"`) in cogs/voicedatabase.py
- [x] T004 [P] [US1] Add `listtext` to `_ICONS`, `_DESCS`, and `_VDB_SECTIONS` Playback group in cogs/general.py

**Checkpoint**: `//listtext` should be fully functional — listing segments, selecting one, and seeing the transcript posted as plain text

---

## Phase 4: Polish & Cross-Cutting Concerns

*(No polish tasks — feature is self-contained with no cross-cutting impact)*

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 3 (User Story 1)**: Can start immediately — no setup or foundational work needed
- T001 and T004 are parallel (different files/locations, no dependencies)
- T002 depends on T001 (builds on the placeholder/footer changes)
- T003 depends on T002 (the command uses the view with mode="text")

### Within User Story 1

```
T001 (view placeholders) ──→ T002 (on_select handler) ──→ T003 (command method)
T004 (help menu)         ──→ (independent, parallel with all above)
```

### Parallel Opportunities

- T001 and T004 can run in parallel (different files)
- T003 and T004 can run in parallel (different files)

---

## Parallel Example: User Story 1

```bash
# These can run in parallel (different files):
Task T001: "Add mode='text' placeholder/footer in _ClipSelectView in cogs/voicedatabase.py"
Task T004: "Add listtext to help menu in cogs/general.py"

# Then sequentially:
Task T002: "Add on_select text mode handler in cogs/voicedatabase.py" (after T001)
Task T003: "Add listtext command method in cogs/voicedatabase.py" (after T002)
```

---

## Implementation Strategy

### MVP (all tasks — feature is small enough to ship as one unit)

1. T001 + T004 in parallel (view text, help menu)
2. T002 (selection handler)
3. T003 (command method)
4. **VALIDATE**: Test `//listtext` in Discord

---

## Notes

- All implementation is in 2 files: `cogs/voicedatabase.py` and `cogs/general.py`
- No database changes, no new dependencies, no schema migrations
- The `listtext` command method is nearly identical to `list_clips` — only the `mode` parameter differs
- Transcript is posted as plain text (no embed) to enable consumption by other bots via `message.content`
- Long transcripts (>2000 chars) are split across multiple messages
