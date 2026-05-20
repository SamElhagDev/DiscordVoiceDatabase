# Research: `//listtext` Command

**Branch**: `001-add-listtext-command` | **Date**: 2026-05-19

## R1: How does the existing `_ClipSelectView` dispatch actions on segment selection?

**Decision**: Extend the existing `mode` parameter with a third value `"text"`.

**Rationale**: `_ClipSelectView.__init__` accepts `mode` (default `"play"`). The `on_select` callback at line 1495 checks `self.mode == "download"` to choose between `_DownloadModal` and `_PlayModal`. Adding an `elif self.mode == "text"` branch before the existing check is the minimal change — no new classes, no refactoring.

**Alternatives considered**:
- *Separate view class*: Would duplicate 100+ lines of pagination/embed logic for no benefit.
- *Callback injection*: Over-engineered for a single new mode.

## R2: Discord message length limits for transcript display

**Decision**: Post transcript as plain text via `message.content`. Split into multiple messages if exceeding 2000-char limit.

**Rationale**: Plain text in `message.content` is trivially consumable by other bots — they just read the string. Embeds require parsing embed objects, which many bot frameworks don't do by default. Most segments are ~60s and produce transcripts well under 2000 chars, so multi-message splitting will be rare.

**Alternatives considered**:
- *Embed*: Richer formatting, but other bots can't easily read embed content from `message.content`.
- *Truncation instead of splitting*: Simpler, but loses data. Splitting preserves the full transcript.

## R3: Segment tuple structure

**Decision**: Transcript text is at index 8 of the segment tuple returned by `get_segments_in_range`.

**Rationale**: Confirmed by reading `database/__init__.py:126-128` — the SELECT returns `(id, guild_id, channel_id, user_id, start_ts, end_ts, file_path, file_size, transcript)`. The existing code in `_ClipSelectView._rebuild_items` already accesses `seg[8]` for transcript display in dropdown descriptions.

## R4: Constitution compliance

**Decision**: No constitution principles are violated.

**Rationale**:
- **Principle I (Consent)**: The command reads existing transcripts — no recording occurs.
- **Principle II (Data Integrity)**: Read-only operation, no mutations.
- **Principle III (Accurate Playback)**: Not applicable (no audio). The transcript shown will be the one stored for the selected segment — accurate by definition.
- **Principle IV (Non-Blocking)**: No audio processing or file I/O. The database query is async.
- **Principle V (User-Facing Clarity)**: Embed provides clear timestamp, user attribution, and transcript text. Empty/missing transcripts are handled with a clear message.
