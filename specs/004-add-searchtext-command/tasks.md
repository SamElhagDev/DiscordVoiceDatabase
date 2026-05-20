# Tasks: `//searchtext` Command

**Input**: Design documents from `specs/004-add-searchtext-command/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/command-schema.md, quickstart.md

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Exact file paths included in every task description

---

## Phase 1: Core Implementation

**Purpose**: Add the `//searchtext` command method

- [x] T001 Add `searchtext` hybrid command method to cogs/voicedatabase.py — after the existing `search_clips` method (around line 671), add a new `@commands.hybrid_command(name="searchtext")` method `search_text` that mirrors `search_clips` exactly (same parameters: user, date, end_date, query; same date parsing; same `search_segments_by_transcript` call; same error handling) but passes `mode="text"` to `_ClipSelectView` and uses `/searchtext` in the logger.info message

---

## Phase 2: Validation

**Purpose**: Verify the implementation works correctly

- [ ] T002 Manual validation (requires running the bot in Discord) — verify `//searchtext @user 2026-05-20 hello` shows the dropdown with matching segments, selecting a segment posts the transcript as plain text, date ranges work, empty query shows error, no results shows yellow embed

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1**: No dependencies — start immediately
- **Phase 2**: Depends on Phase 1

### Execution

```
Phase 1: T001
    ↓
Phase 2: T002
```

---

## Notes

- Single-file change: only `cogs/voicedatabase.py` is modified
- No changes to `_ClipSelectView`, database layer, or any other component
- No automated tests — validation is manual (consistent with project pattern)
