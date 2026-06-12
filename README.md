# Discord Voice Database

A Discord bot that records voice channel audio on a per-user, consent-based basis and lets you browse, search, play back, download, and favorite clips on demand. Built with [discord.py](https://github.com/Rapptz/discord.py), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), and FFmpeg.

## Features

- **Consent-based recording** — Users must explicitly opt in with `/join` before any audio is captured. Opt out anytime with `/leave`.
- **Per-user segmented storage** — Audio is recorded as individual segments per user (default 60s), stored as OGG/Opus files indexed in SQLite.
- **Resilient capture** — A per-user jitter buffer reorders late packets, and Opus PLC/FEC conceal packet loss, so audio from users on spotty connections stays intelligible.
- **Silent auto-recovery** — A health monitor detects a stalled voice connection and reconnects in place (no visible leave/rejoin) whenever possible, falling back to a full reconnect only if needed.
- **Voice Activity Detection** — Only segments with actual speech are saved. Silent periods are filtered out by both a bytes-per-second threshold and blank-transcript detection.
- **Speech-to-text transcription** — Segments are automatically transcribed with faster-whisper after recording. Transcripts appear in the clip browser and power search.
- **Transcript search** — `/search`, `/searchclips`, and `/searchtext` find segments by what was said, then play, download, or display the match.
- **Clip retrieval** — Browse segments by user and date range, then play them in a voice channel or download them as OGG files with configurable duration and offset.
- **Personal favorites** — Save any clip with the ⭐ button on a clip menu (no playback required), then revisit them with `/favoritesclip` (download) or `/favoriteslist` (play in VC). Favorites are per-user.
- **Multi-day aware browsing** — When a date-range result spans more than one day, the segment menus show the date alongside the time.
- **Auto-join** — Configure a primary channel and the bot automatically rejoins and resumes recording when members are present.
- **DAVE E2EE support** — Handles Discord's end-to-end encrypted voice channels via the `davey` library.
- **Role-based access control** — Optionally restrict all bot commands to members holding a specific role.
- **Backfill transcription** — `/transcribe` queues transcription for all past recordings that haven't been processed yet, with live progress updates.
- **Automatic cleanup** — Old recordings are purged based on a configurable per-guild retention policy (default 7 days).
- **Rotating logs** — Activity is written to `logs/DiscordVoiceDatabase.log` with size-based rotation.
- **Docker support** — Run with `docker compose up` for easy deployment.

## Requirements

- Python 3.10+ (the Docker image uses 3.12)
- [FFmpeg](https://ffmpeg.org/download.html) and libopus installed and on PATH
- A Discord bot token with the **Server Members** and **Message Content** privileged intents enabled

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/your-username/DiscordVoiceDatabase.git
cd DiscordVoiceDatabase
python -m pip install -r requirements.txt
```

### 2. Configure environment

Copy the example env file and fill in your bot token:

```bash
cp .env.example .env
```

Minimal `.env`:

```env
DiscordVoiceDatabase_TOKEN=your_discord_bot_token_here
DiscordVoiceDatabase_PREFIX=!
```

### 3. Run the bot

```bash
python bot.py
```

### Docker

```bash
docker compose up -d --build
```

Recordings, clips, and the database are persisted via Docker volumes.

## Configuration

All environment variables are prefixed `DiscordVoiceDatabase_` (the one exception is `WHISPER_MODEL`). Defaults are fine for most setups — only `DiscordVoiceDatabase_TOKEN` is required.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DiscordVoiceDatabase_TOKEN` | **Yes** | — | Discord bot token |
| `DiscordVoiceDatabase_PREFIX` | No | `!` | Prefix for text commands (slash commands always work) |
| `DiscordVoiceDatabase_INVITE_LINK` | No | — | Bot invite link surfaced in some embeds |
| `DiscordVoiceDatabase_VERSION` | No | `1.0.0` | Version string shown in the `/help` footer |
| `DiscordVoiceDatabase_ROLE_ID` | No | — | If set, only members with this role ID can use bot commands. Leave unset to allow everyone. |
| `DiscordVoiceDatabase_RETENTION_DAYS` | No | `7` | Days to keep recordings before automatic deletion |
| `DiscordVoiceDatabase_DB_PATH` | No | `./data/database.db` | SQLite database path |
| `DiscordVoiceDatabase_RECORDINGS_PATH` | No | `./recordings` | Per-user segment storage |
| `DiscordVoiceDatabase_CLIPS_PATH` | No | `./clips` | Generated clip output |
| `WHISPER_MODEL` | No | `tiny` | Whisper model size: `tiny`, `base`, `small`, `medium`, `large-v3` |

### Access control

By default, anyone in the server can use the bot. To restrict it, set `DiscordVoiceDatabase_ROLE_ID` to a Discord role's ID — only members holding that role will be able to run commands; everyone else gets a polite refusal. This is checked for every command in the voice cog.

## Commands

Commands are **hybrid** — each works both as a slash command (`/command`) and with the configured text prefix (e.g. `!command`). In the usage below, `<>` marks required parameters and `[]` optional ones. `date`/`end_date` are Eastern `YYYY-MM-DD` (default: today).

### Participation

| Command | Description |
|---------|-------------|
| `/join` | Opt in to voice recording |
| `/leave` | Opt out of voice recording |
| `/participants` | List all opted-in users in the server |

### Recording

| Command | Description | Permission |
|---------|-------------|------------|
| `/record` | Start recording in your current voice channel (or the configured primary channel) | Manage Server |
| `/stoprecord` | Stop recording and disconnect | Manage Server |
| `/recordingstatus` | Show recording status, channel, and talk-time stats | — |

### Browsing, playback & clips

Each of these opens an interactive, paginated dropdown of matching segments. Selecting one performs the action below. Every menu also has a **⭐ Favorite** button to save a clip without playing it.

| Command | Description |
|---------|-------------|
| `/listclips <user> [date] [end_date]` | Browse segments, then **play** the selected one in VC |
| `/clip <user> [date] [end_date]` | Browse segments, then **download** the selected one as a file |
| `/listtext <user> [date] [end_date]` | Browse segments, then **view the transcript** text |
| `/playclip <user> <start> [minutes]` | Play a clip directly in VC from a precise start timestamp (`YYYY-MM-DDTHH:MM:SS`, default 10 minutes) |
| `/stop` | Stop the currently playing clip |

### Search

Search a user's transcripts for text, then act on a match.

| Command | Description |
|---------|-------------|
| `/search <user> [date] [end_date] [query]` | Search transcripts, then **play** the selected match |
| `/searchclips <user> [date] [end_date] [query]` | Search transcripts, then **download** the selected match |
| `/searchtext <user> [date] [end_date] [query]` | Search transcripts, then **view the transcript** text |

### Favorites

| Command | Description |
|---------|-------------|
| `/favoritesclip` | Browse your saved favorites and **download** one |
| `/favoriteslist` | Browse your saved favorites and **play** one in VC |

Save a favorite by clicking **⭐ Favorite** on any clip menu, picking a segment, and confirming the duration — no playback or download required. Each favorite is personal to you; manage them (including removal) from the favorites menus.

### Settings

| Command | Description | Permission |
|---------|-------------|------------|
| `/setchannel <channel>` | Set the primary recording channel for auto-join | Manage Server |
| `/retention <days>` | Set how many days recordings are kept before deletion (per guild) | Manage Server |

### Transcription & diagnostics

| Command | Description | Permission |
|---------|-------------|------------|
| `/transcribe` | Backfill transcriptions for all past recordings that have none, with live progress | Manage Server |
| `/perfstats [days]` | Show running averages of processing time per pipeline stage | — |

### General

| Command | Description |
|---------|-------------|
| `/help` | Show the grouped command menu with parameters |
| `/ping` | Check bot latency |

## Architecture

```
DiscordVoiceDatabase/
├── bot.py                  # Entry point, bot setup, logging, DB init, cog loading
├── cogs/
│   ├── general.py          # Help (grouped, with parameters), ping
│   ├── owner.py            # Sync, load/unload/reload cogs, shutdown
│   └── voicedatabase.py    # Recording, browsing, search, playback, favorites
├── database/
│   ├── __init__.py         # DatabaseManager (async SQLite, WAL)
│   └── schema.sql          # consent, segments, recording_settings, perf_logs, favorites
├── recording/
│   ├── recorder.py         # VoiceRecorder: per-user PCM capture, jitter buffer, PLC/FEC
│   ├── retriever.py        # ClipRetriever: segment stitching/trimming via FFmpeg
│   ├── transcriber.py      # Background faster-whisper transcription worker
│   └── cleanup.py          # Automatic old-recording purge (per-guild retention)
├── assets/chimes/          # Start/end chimes played during clip playback
├── logs/                   # Rotating log files (DiscordVoiceDatabase.log)
├── specs/                  # Feature specs and implementation plans
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

### How recording works

1. A user with Manage Server runs `/record`.
2. The bot joins the voice channel and listens to all opted-in users.
3. Each user's audio is decoded to raw PCM (48kHz, stereo, 16-bit). A per-user jitter buffer reorders late packets; Opus PLC/FEC conceal genuine loss.
4. A voice activity detector filters out silence — only frames with speech are written.
5. Every 60 seconds, the buffered PCM is flushed to disk and remuxed to OGG/Opus in the background.
6. After remux, the OGG is queued for transcription via faster-whisper (runs in a background thread, never blocking the event loop).
7. Segment metadata (timestamps, file paths, transcripts) is stored in SQLite for fast lookup.
8. A cleanup task runs periodically, deleting segments older than each guild's retention period.

If voice packets stop arriving while members are still present, the health monitor first attempts a **soft reconnect** (refreshing the voice session in place) and only falls back to a full reconnect if that fails.

### How browsing & playback work

1. A browse/search command queries segments by user, date range, and (for search) transcript text, filters out silent/blank segments, and presents them in a paginated dropdown with transcript previews. When results span multiple days, each entry shows its date.
2. Selecting a segment opens a modal to configure duration and start offset (or, in favorite mode, saves it to your favorites).
3. The bot retrieves the clip via FFmpeg, trimming and concatenating segments as needed.
4. The clip is either streamed to the voice channel via `FFmpegOpusAudio` (play) or uploaded as an OGG file (download).

## Discord Bot Intents

The bot requires the following privileged intents enabled in the [Discord Developer Portal](https://discord.com/developers/applications):

- **Server Members Intent** — to track voice channel membership and resolve display names
- **Message Content Intent** — for prefix commands

## License

See [LICENSE.md](LICENSE.md) for details.
