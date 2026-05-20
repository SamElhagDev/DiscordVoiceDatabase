# Command Contract: `//searchtext`

## Registration

```python
@commands.hybrid_command(
    name="searchtext",
    description="Search recordings by transcript and view the matching text.",
)
@app_commands.describe(
    user="The user to search recordings for",
    date="Date or start date in Eastern time (YYYY-MM-DD)",
    query="Text to search for in transcripts",
    end_date="End date for range search (YYYY-MM-DD, optional)",
)
```

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| user | discord.User | Yes | The user whose recordings to search |
| date | str | Yes | Start date in YYYY-MM-DD format (Eastern time) |
| end_date | str | No | End date for range search (YYYY-MM-DD). If provided but not a valid date, treated as part of query. |
| query | str | No | Text to search for in transcripts (keyword argument) |

## Behavior

1. **Guild check**: Command only works in a server context.
2. **Date parsing**: Parses `date` as YYYY-MM-DD. Returns error embed (red, 0xED4245) on invalid format.
3. **End date parsing**: If `end_date` is provided but invalid, concatenates it with `query` and continues as single-date search.
4. **Date range validation**: If `end_date` < `date`, returns error embed.
5. **Query validation**: If `query` is empty after parsing, returns error embed.
6. **Search**: Calls `search_segments_by_transcript(user_id, start_ts, end_ts, query, guild_id)`.
7. **No results**: Returns yellow embed (0xFEE75C) with search context.
8. **Results found**: Shows `_ClipSelectView` with `mode="text"`, same pagination/dropdown as `//search`.

## Selection Behavior (via `_ClipSelectView` mode="text")

- Posts transcript as plain text message (not embed) so other bots can consume via `message.content`.
- Includes header: `[DisplayName — HH:MM:SS AM/PM]`
- Splits messages at 2000-character boundary if transcript is long.
- Missing/blank transcript: ephemeral error "No transcript available for this segment."

## Differences from `//search`

| Aspect | `//search` | `//searchtext` |
|--------|-----------|----------------|
| View mode | `"play"` (default) | `"text"` |
| On selection | Opens play modal (duration, offset) | Posts transcript as text |
| Audio access | Yes (via retriever) | No |
