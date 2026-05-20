# Quickstart: `//searchtext` Command

## What to build

A single new hybrid command method `search_text` in `cogs/voicedatabase.py` that mirrors `search_clips` but passes `mode="text"` to `_ClipSelectView`.

## File to modify

- `cogs/voicedatabase.py` — add one new command method (~40 lines)

## Implementation steps

1. After the existing `search_clips` method (around line 671), add a new `@commands.hybrid_command` with `name="searchtext"`.
2. Copy the body of `search_clips` exactly.
3. Change the `_ClipSelectView` constructor call to include `mode="text"`.
4. Update the logger.info message to say `/searchtext` instead of `/search`.

## No changes needed to

- `_ClipSelectView` — already supports `mode="text"`
- `database/__init__.py` — `search_segments_by_transcript` already exists
- Any other files

## How to test

1. Start the bot
2. In a server with recorded segments, run: `//searchtext @user 2026-05-20 hello`
3. Verify the dropdown appears with matching segments
4. Select a segment — transcript should appear as a plain text message
5. Test date range: `//searchtext @user 2026-05-18 2026-05-20 hello`
6. Test empty query: `//searchtext @user 2026-05-20` — should show error
7. Test no results: `//searchtext @user 2026-05-20 xyznonexistent` — should show yellow embed
