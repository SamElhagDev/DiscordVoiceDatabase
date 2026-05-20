# Quickstart: `//listtext` Command

## What This Feature Does

Adds a `//listtext` command that shows the same segment-browsing UI as `//listclips`, but when you select a segment it posts the transcript text in the channel instead of downloading/playing audio.

## Files to Modify

1. **`cogs/voicedatabase.py`** — the only file that needs changes:
   - Add the `listtext` hybrid command method (~25 lines, mirrors `list_clips`)
   - Extend `_ClipSelectView.on_select` to handle `mode="text"` (~15 lines)
   - Update `_ClipSelectView._rebuild_items` placeholder text for "text" mode (~1 line)
   - Update `_ClipSelectView.build_embed` footer text for "text" mode (~1 line)

## Implementation Sketch

```python
# 1. New command (mirrors list_clips exactly, passes mode="text")
@commands.hybrid_command(name="listtext", description="...")
@app_commands.describe(user="...", date="...")
async def list_text(self, context, user, date):
    # Same segment query + filter as list_clips
    # Pass mode="text" to _ClipSelectView

# 2. In _ClipSelectView.on_select, add before existing mode checks:
if self.mode == "text":
    transcript = seg[8] if len(seg) > 8 and seg[8] else None
    if not transcript or transcript == "Blank":
        await interaction.response.send_message("No transcript available.", ephemeral=True)
        return
    # Post as plain text (no embed) so other bots can read message.content
    # Split into 2000-char chunks if needed
```

## No Changes Needed

- **Database layer**: Reuses existing `get_segments_in_range`
- **Schema**: No migrations
- **Other cogs**: No cross-cog dependencies
- **Bot startup**: Command auto-discovered via cog loading
