# Research: `//searchtext` Command

## Summary

No external research required. This feature is a composition of two existing, proven patterns within the codebase.

## Decision: Command Implementation Approach

**Decision**: Duplicate the `search_clips` method body, changing only the `mode` parameter passed to `_ClipSelectView` from `"play"` (default) to `"text"`.

**Rationale**: This mirrors exactly how `//listtext` was created relative to `//listclips` — same query, different view mode. The `_ClipSelectView` class already handles `mode="text"` with transcript display, chunking, and error handling. No changes to the view class, database layer, or any other component are needed.

**Alternatives considered**:
- Parameterizing `//search` with a `--text` flag: Rejected because hybrid commands don't support boolean flags cleanly in Discord's slash command UI, and it would complicate the existing `//search` command's interface.
- Creating a shared helper method for `search_clips` and `search_text`: Rejected because the duplication is minimal (~30 lines) and the two commands may diverge in the future (e.g., different logging, different error messages). The existing pattern in the codebase (`list_clips` and `list_text` are separate methods) favors explicit separate methods.

## Decision: Query Reuse

**Decision**: Reuse `search_segments_by_transcript` as-is. No database changes needed.

**Rationale**: The method already filters out blank/null transcripts via SQL (`transcript != 'Blank'` and `transcript IS NOT NULL`), returns the same tuple shape as other segment queries, and supports both single-date and date-range searches. The `mode="text"` path in `on_select` already handles the transcript display from `seg[8]`.

**Alternatives considered**: None — the existing method is a perfect fit.
