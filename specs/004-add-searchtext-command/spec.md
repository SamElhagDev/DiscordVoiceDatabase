# Feature Specification: `//searchtext` Command

**Branch**: `main` | **Date**: 2026-05-20

## Overview

Add a new `//searchtext` hybrid command that searches recordings by transcript text — identical to `//search` in its query parameters and search logic — but instead of showing results in play mode (which triggers an audio playback modal on selection), posts the selected segment's transcript text into the channel, identical to how `//listtext` behaves.

## Requirements

### Functional

1. The command accepts the same parameters as `//search`: `user` (discord.User), `date` (YYYY-MM-DD string), optional `end_date` (YYYY-MM-DD string for range searches), and `query` (search text).
2. Segments are queried using `search_segments_by_transcript` — the same database method used by `//search`.
3. Date parsing, date range handling, and the end_date-as-query fallback logic are identical to `//search`.
4. The segment selection UI (dropdown, pagination, embed listing) is identical to `//search`, but uses `mode="text"`.
5. When a user selects a segment from the dropdown, the bot posts the segment's transcript as a plain text message in the channel (same behavior as `//listtext` selection).
6. If the selected segment has no transcript, the bot responds with a "No transcript available" ephemeral message.
7. Long transcripts exceeding Discord's 2000-character message limit are split across multiple messages.

### Non-Functional

- No new database queries or schema changes required — reuses `search_segments_by_transcript`.
- No new UI components — `_ClipSelectView` already supports `mode="text"`.
- No new permissions required — mirrors `//search` which has no permission guards.
- The `//search` command remains unchanged and continues to use `mode="play"`.
