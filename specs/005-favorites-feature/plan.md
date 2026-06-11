# Implementation Plan: Favorites Feature

**Feature**: `005-favorites-feature` | **Date**: 2026-06-10 | **Branch**: `main`

## Overview

Let each user keep a personal list of favorite audio clips. Favorites live in a new
`favorites` table keyed by Discord user ID (per-user, per-guild). Any clip surfaced in
an audio menu (`//listclips`, `//clip`, `//search`, `//searchclips`, `//playclip`) can be
saved with a single "⭐ Add to Favorites" button, and a new `//favorites` command lets a
user browse and re-download (or remove) their saved clips.

## Key Design Decisions

### 1. Favoriting must NOT require playing the clip (and modals can't hold buttons)
Two constraints shape the placement:
- Discord modals (`discord.ui.Modal`) accept **only text inputs** — no buttons inside a modal.
- A user must be able to favorite a clip **without playing it through the voice channel**
  (playback is disruptive to everyone in VC; bookmarking should be silent).

Therefore the favorite action lives on the **first dialog** — the `_ClipSelectView` segment
menu — *before* any play/download happens. A "⭐ Favorite" button toggles the view into
favorite mode; picking a segment then opens a small **favorite modal** (duration + offset —
text inputs only, which modals allow) that saves the clip with an ephemeral confirmation. No
audio plays, no file is sent.

Because every audio menu (`//listclips`, `//clip`, `//search`, `//searchclips`, `//playclip`)
funnels through the shared `_ClipSelectView`, adding the toggle there covers **all** menus
with one change — no per-command wiring. Favorite mode is independent of the view's existing
`mode` (`play`/`download`/`text`), so the normal action still works when the toggle is off.

### 2. What a favorite stores
A favorite captures the *derived clip*, not just the segment: `segment_id` + `offset_sec`
+ `duration_min`, taken from the favorite modal's inputs. This makes `//favorites` a
one-click re-download of the identical clip. A cached `label` (target username + time +
transcript snippet) is stored so the favorites list renders without joins and still reads
sensibly if the underlying segment is later cleaned up. The favorite modal defaults
duration to the segment's natural length and offset to 0, so the common case is just
"click ⭐ → pick segment → submit."

### 3. Retention interaction (flagged)
Segments are deleted after `retention_days` (`recording/cleanup.py`). Favorites do **not**
block cleanup in this plan — keeps the cleanup query untouched. If a favorited clip's audio
has been purged, `//favorites` shows the entry with an "audio expired" note instead of a
file. *(Open question below if you'd rather favorites pin segments against deletion.)*

### 4. Uniqueness
`UNIQUE(user_id, segment_id, offset_sec, duration_min)` — re-favoriting the exact same clip
is a no-op (button reports "already in favorites"); different offset/duration of the same
segment is allowed.

## Data Model

Add to `database/schema.sql` (uses `CREATE TABLE IF NOT EXISTS`, so it applies on next
startup via `_init_schema` → `executescript`; **no migration code needed**):

```sql
CREATE TABLE IF NOT EXISTS `favorites` (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,          -- Discord ID of the favorite's owner
    guild_id INTEGER NOT NULL,
    segment_id INTEGER NOT NULL,       -- anchor segment (segments.id)
    target_user_id INTEGER NOT NULL,   -- whose audio the clip is of
    offset_sec INTEGER NOT NULL DEFAULT 0,
    duration_min INTEGER NOT NULL DEFAULT 1,
    label TEXT,                        -- cached display string
    created_at REAL NOT NULL,
    UNIQUE(user_id, segment_id, offset_sec, duration_min)
);

CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id, guild_id, created_at);
```

## DatabaseManager methods (`database/__init__.py`)

New "Favorites operations" section, following the existing `async with self._lock` write /
lock-free read pattern:

| Method | Purpose |
|---|---|
| `add_favorite(user_id, guild_id, segment_id, target_user_id, offset_sec, duration_min, label) -> bool` | `INSERT ... ON CONFLICT DO NOTHING`; return `True` if inserted, `False` if duplicate (via `cursor.rowcount`). |
| `get_favorites(user_id, guild_id) -> list` | Rows ordered by `created_at DESC` for the favorites menu. |
| `get_favorite(user_id, favorite_id) -> tuple \| None` | Single favorite (ownership-checked) for re-deriving a clip. |
| `remove_favorite(user_id, favorite_id) -> bool` | Delete one favorite the user owns; `True` if a row was removed. |

All filter on `user_id` so a user can only ever read/modify their own favorites.

## UI changes (`cogs/voicedatabase.py`)

**⭐ toggle on `_ClipSelectView`** (the shared first-dialog segment menu):
- Add a `favorite_mode: bool` flag (default `False`) and a "⭐ Favorite" button rendered in
  `_rebuild_items()` for `play`/`download` modes (not `text`).
- Clicking the button flips `favorite_mode`, re-renders the dropdown with placeholder
  "Pick a segment to favorite…", and relabels the button ("⭐ Favorite" ↔ "⬅ Back").
- In `on_select`, branch first on `favorite_mode`: if set, open `_FavoriteModal` instead of
  the play/download modal. This keeps the existing `mode` routing untouched when the toggle
  is off.

**New `_FavoriteModal(discord.ui.Modal)`** — duration + offset text inputs (mirrors
`_DownloadModal`), constructed with `cog, owner_id, guild_id, seg, target_user`. On submit:
build the cached `label`, call `add_favorite(...)`, reply ephemerally ("⭐ Saved to favorites"
/ "Already in your favorites"). **No playback, no file send.**

**New `_FavoriteSelectView`** for `//favorites` — mirror `_ClipSelectView` (dropdown +
pagination + `on_timeout`), built from favorite rows. Selecting an entry re-derives the clip
with `retriever.retrieve_clip(... anchor_seg, offset_sec, duration)` and sends it as a file.
Include a "🗑 Remove" toggle: in remove mode, selecting an entry calls `remove_favorite`
instead of downloading.

## New command (`cogs/voicedatabase.py`)

```
//favorites            -> hybrid_command, guild-only via existing cog_check
```
Lists the invoker's favorites in the current guild via `_FavoriteSelectView`. Empty state:
friendly "You have no favorites yet — use the ⭐ button on any clip" embed.

## Help registration (`cogs/general.py`)

Add `favorites` to `_ICONS` (⭐), `_DESCS` ("Browse your saved clips"), and the
`🎵 Playback` section list in `_VDB_SECTIONS`.

## Files to touch

| File | Change |
|---|---|
| `database/schema.sql` | Add `favorites` table + index |
| `database/__init__.py` | 4 favorites methods |
| `cogs/voicedatabase.py` | ⭐ toggle on `_ClipSelectView`, `_FavoriteModal`, `_FavoriteSelectView`, `//favorites` command |
| `cogs/general.py` | Help menu entries |

No changes to `bot.py`, `recorder.py`, `retriever.py`, `cleanup.py`, or `transcriber.py`.

## Task list (ordered)

1. **Schema** — add `favorites` table + index to `database/schema.sql`.
2. **DB layer** — implement `add_favorite`, `get_favorites`, `get_favorite`, `remove_favorite`
   in `database/__init__.py`; verify table is created on a fresh DB startup.
3. **Favorite toggle + modal** — add `favorite_mode` + "⭐ Favorite" button to
   `_ClipSelectView`; add `_FavoriteModal` (duration/offset → `add_favorite`, no playback).
4. **Verify silent favoriting** — confirm favoriting from a `play`-mode menu (`//listclips`)
   saves without the clip playing in VC.
5. **Favorites menu** — add `_FavoriteSelectView` (download + remove modes).
6. **Command** — add `//favorites` hybrid command using the select view.
7. **Help** — register `favorites` in `cogs/general.py`.
8. **Verify** — `py_compile` all touched files; manual smoke test: favorite a clip from
   `//clip` and `//search`, list with `//favorites`, re-download, remove.

## Edge cases

- **Expired audio**: favorite row survives, `retrieve_clip` returns `None` → menu shows
  "audio no longer available," offer to remove.
- **Duplicate favorite**: `add_favorite` returns `False` → button says "Already favorited."
- **Cross-user**: every query filters on `user_id`; one user never sees/deletes another's.
- **Ongoing segment** (`end_ts IS NULL`): allowed; duration defaults as in existing modals.

## Open questions (non-blocking; sensible defaults chosen)

1. **Pin against cleanup?** Default: favorites do *not* prevent segment deletion. Alternative:
   `cleanup.py` excludes segment IDs present in `favorites`. (More state, slower retention.)
2. **Favorite scope when audio expires** — default keeps the bookmark with a note. Alternative:
   auto-prune favorites whose segment was deleted.
3. **Play-from-favorites** — plan defaults `//favorites` selection to *download*. Could add a
   play mode toggle mirroring `//playclip`.

## Out of scope

Sharing favorites between users, folders/tags, renaming, favorite limits/quotas.
