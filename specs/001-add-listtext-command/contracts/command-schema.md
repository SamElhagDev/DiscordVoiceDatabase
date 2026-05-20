# Command Contract: `//listtext`

## Command Definition

| Property | Value |
|----------|-------|
| Name | `listtext` |
| Type | hybrid_command (prefix + slash) |
| Description | "List recorded segments and view the transcript text for a user on a given day." |
| Permissions | None (same as `//listclips`) |

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| user | discord.User | Yes | The user whose transcripts to list |
| date | str | Yes | Date in Eastern time (YYYY-MM-DD) |

## Response Flow

1. **Initial response**: Embed listing segments with dropdown selector (identical to `//listclips`)
   - Dropdown placeholder: "Pick a segment to view transcript..."
   - Embed footer: "{n} segment(s) — Select one below to view its transcript"

2. **On segment selection**: Plain text message posted in channel:
   - Just the raw transcript text — no embed, no formatting wrapper
   - If transcript exceeds 2000 chars, split across multiple messages
   - This makes the content available in `message.content` for other bots to consume

3. **Edge cases**:
   - No segments found: Embed with "No recordings with voice activity found for {user} on {date}."
   - No transcript on selected segment: Ephemeral message "No transcript available for this segment."
   - Invalid date format: Embed with format guidance
