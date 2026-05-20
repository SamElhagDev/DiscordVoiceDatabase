# Feature Specification: `//listtext` Command

**Branch**: `001-add-listtext-command` | **Date**: 2026-05-19

## Overview

Add a new `//listtext` hybrid command that displays recorded segments for a user on a given day — identical to `//listclips` in its segment listing and dropdown selection UI — but instead of triggering audio download or playback, posts the selected segment's transcript text as a message in the channel.

## Requirements

### Functional

1. The command accepts the same parameters as `//listclips`: `user` (discord.User) and `date` (YYYY-MM-DD string).
2. Segments are queried and filtered identically to `//listclips` (same date range, same "Blank" transcript filtering).
3. The segment selection UI (dropdown, pagination, embed listing) is identical to `//listclips`.
4. When a user selects a segment from the dropdown, the bot posts the segment's transcript as a plain text message in the channel (no modal popup, no embed). This ensures other bots can easily consume the transcript via `message.content`.
5. If the selected segment has no transcript, the bot responds with a "No transcript available" ephemeral message.
6. Long transcripts exceeding Discord's 2000-character message limit should be split across multiple messages.

### Non-Functional

- No new database queries or schema changes required — reuses `get_segments_in_range`.
- No audio file access needed — transcript is already stored in the segment tuple at index 8.
- No new permissions required — mirrors `//listclips` which has no permission guards.
