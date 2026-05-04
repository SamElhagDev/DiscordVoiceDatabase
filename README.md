# Discord Voice Database

A Discord bot that records voice channel audio on a per-user, consent-based basis and lets you retrieve, browse, and play back clips on demand. Built with [discord.py](https://github.com/Rapptz/discord.py) and FFmpeg.

## Features

- **Consent-based recording** — Users must explicitly opt in with `/join` before any audio is captured. Opt out anytime with `/leave`.
- **Per-user segmented storage** — Audio is recorded as individual segments per user (default 60s), stored as OGG/Opus files indexed in SQLite.
- **Voice Activity Detection** — Only segments with actual speech are saved. Silent periods are automatically filtered out.
- **Clip retrieval** — Retrieve audio clips by user and time range, delivered as OGG files or played directly in a voice channel.
- **Interactive browsing** — `/listclips` shows a paginated dropdown of all segments for a user on a given day. Select one to play it in VC with configurable duration and offset.
- **Auto-join** — Configure a primary channel and the bot will automatically rejoin and resume recording when members are present.
- **DAVE E2EE support** — Handles Discord's end-to-end encrypted voice channels via the `davey` library.
- **Automatic cleanup** — Old recordings are purged based on a configurable retention policy (default 7 days).
- **Docker support** — Run with `docker compose up` for easy deployment.

## Requirements

- Python 3.12+
- [FFmpeg](https://ffmpeg.org/download.html) installed and on PATH
- A Discord bot token with voice and message intents

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

```env
TOKEN=your_discord_bot_token_here
PREFIX=!
```

Optional overrides (defaults are fine for most setups):

```env
DB_PATH=./data/database.db
RECORDINGS_PATH=./recordings
CLIPS_PATH=./clips
RETENTION_DAYS=7
```

### 3. Run the bot

```bash
python bot.py
```

### Docker

```bash
docker compose up -d --build
```

Recordings and database are persisted via Docker volumes.

## Commands

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
| `/recordingstatus` | Show whether the bot is currently recording and in which channel | — |

### Clip Retrieval

| Command | Description |
|---------|-------------|
| `/clip @user 2026-05-03T14:00:00 10` | Retrieve a 10-minute clip starting at the given timestamp and send it as a file |
| `/listclips @user 2026-05-03` | Browse all segments for a user on a given day with an interactive dropdown. Select a segment to play it in VC. |
| `/playclip @user 2026-05-03T14:00:00 10` | Play a clip directly in your current voice channel |

### Settings

| Command | Description | Permission |
|---------|-------------|------------|
| `/setchannel #channel` | Set the primary recording channel for auto-join | Manage Server |
| `/retention 14` | Set how many days recordings are kept before automatic deletion | Manage Server |

### General

| Command | Description |
|---------|-------------|
| `/help` | List all available commands |
| `/ping` | Check if the bot is alive |

## Architecture

```
DiscordVoiceDatabase/
├── bot.py                  # Entry point, bot setup, cog loading
├── cogs/
│   ├── general.py          # Help, ping
│   ├── owner.py            # Sync, load/reload cogs, shutdown
│   └── voicedatabase.py    # All recording and playback commands
├── database/
│   ├── __init__.py         # DatabaseManager (async SQLite)
│   └── schema.sql          # Table definitions
├── recording/
│   ├── recorder.py         # VoiceRecorder, per-user PCM capture with VAD
│   ├── retriever.py        # ClipRetriever, segment stitching via FFmpeg
│   └── cleanup.py          # Automatic old recording purge
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

### How recording works

1. A user with Manage Server runs `/record`
2. The bot joins the voice channel and listens to all opted-in users
3. Each user's audio is captured as raw PCM (48kHz, stereo, 16-bit)
4. A voice activity detector filters out silence — only frames with speech are written
5. Every 60 seconds, the buffered PCM is flushed to disk and remuxed to OGG/Opus in the background
6. Segment metadata (timestamps, file paths) is stored in SQLite for fast lookup
7. A cleanup task runs hourly, deleting segments older than the retention period

### How playback works

1. `/listclips` queries segments by user and date, presents them in a paginated dropdown
2. Selecting a segment opens a modal to configure duration and start offset
3. The bot retrieves the clip via FFmpeg (trimming/concatenating segments as needed)
4. The OGG/Opus file is streamed to the voice channel via `FFmpegOpusAudio`

## Discord Bot Intents

The bot requires the following privileged intents enabled in the [Discord Developer Portal](https://discord.com/developers/applications):

- **Server Members Intent** — to track voice channel membership
- **Message Content Intent** — for prefix commands

## License

See [LICENSE.md](LICENSE.md) for details.
