# Quickstart: Implement Performance Logging

**Branch**: `003-performance-logging` | **Date**: 2026-05-20

## Files to change

| File | Change |
|------|--------|
| `database/schema.sql` | Add `perf_logs` table + indexes |
| `database/__init__.py` | Add `log_perf`, `get_perf_stats`, `delete_perf_logs_by_segment_ids` methods |
| `recording/recorder.py` | Add timers around `_rotate_user` and `_remux_worker`; pass `db` ref to remux path |
| `recording/transcriber.py` | Add timer around `_transcribe` call in `_worker` |
| `recording/cleanup.py` | Add `delete_perf_logs_by_segment_ids` call before segment deletion |
| `cogs/voicedatabase.py` | Add `//perfstats` command |

## Step-by-step

### 1. Schema — `database/schema.sql`

Append after the `recording_settings` table:

```sql
CREATE TABLE IF NOT EXISTS perf_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    segment_id INTEGER NOT NULL,
    method TEXT NOT NULL,
    duration_sec REAL NOT NULL,
    audio_duration_sec REAL NOT NULL,
    logged_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_perf_logs_method_time
    ON perf_logs(method, logged_at);

CREATE INDEX IF NOT EXISTS idx_perf_logs_segment
    ON perf_logs(segment_id);
```

### 2. Database methods — `database/__init__.py`

Add three methods to `DatabaseManager`:

```python
async def log_perf(self, segment_id: int, method: str,
                   duration_sec: float, audio_duration_sec: float):
    async with self._lock:
        await self.connection.execute(
            "INSERT INTO perf_logs(segment_id, method, duration_sec, audio_duration_sec, logged_at) VALUES (?, ?, ?, ?, ?)",
            (segment_id, method, duration_sec, audio_duration_sec, time.time()),
        )
        await self.connection.commit()

async def get_perf_stats(self, since_ts: float, since_24h_ts: float) -> list:
    rows = await self.connection.execute(
        """SELECT method, COUNT(*) AS cnt,
                  AVG(duration_sec * 60.0 / audio_duration_sec) AS avg_per_60s,
                  AVG(CASE WHEN logged_at >= ? THEN duration_sec * 60.0 / audio_duration_sec END) AS avg_24h
           FROM perf_logs
           WHERE logged_at >= ? AND audio_duration_sec > 0
           GROUP BY method ORDER BY method""",
        (since_24h_ts, since_ts),
    )
    async with rows as cursor:
        return await cursor.fetchall()

async def delete_perf_logs_by_segment_ids(self, segment_ids: list):
    if not segment_ids:
        return
    async with self._lock:
        placeholders = ",".join("?" for _ in segment_ids)
        await self.connection.execute(
            f"DELETE FROM perf_logs WHERE segment_id IN ({placeholders})",
            segment_ids,
        )
        await self.connection.commit()
```

### 3. Recorder timers — `recording/recorder.py`

**In `_rotate_user`** — wrap the disk flush + DB calls:

```python
# At the top of the rotate work (after lock release, before flush_to_disk):
rotate_start = time.time()

# After db.close_segment and before enqueue:
rotate_dur = time.time() - rotate_start
audio_dur = end_ts - start_ts
if audio_dur > 0:
    await self.db.log_perf(segment_id, "rotate", rotate_dur, audio_dur)
```

**In `_remux_worker`** — wrap the FFmpeg call:

```python
remux_start = time.time()
await asyncio.to_thread(self._remux_pcm_to_ogg, pcm_path, ogg_path)
remux_dur = time.time() - remux_start

# After file size update, get audio duration from DB or compute from segment:
# Use (ogg file duration) or approximate from PCM size / BYTES_PER_SEC
audio_dur = os.path.getsize(pcm_path_original) / BYTES_PER_SEC if pcm_size > 0 else 60.0
await self.db.log_perf(segment_id, "remux", remux_dur, audio_dur)
```

Note: Since the PCM file is deleted after remux, capture its size before deletion
to compute audio duration. Alternatively, query the segment's `start_ts`/`end_ts`.

### 4. Transcriber timer — `recording/transcriber.py`

**In `_worker`** — wrap the transcribe call:

```python
t0 = time.time()
transcript = await asyncio.to_thread(self._transcribe, ogg_path)
duration = time.time() - t0

# Get segment's audio duration from DB
# Option: pass audio_duration through the queue, or query it
await self.db.log_perf(segment_id, "transcribe", duration, audio_duration)
```

To get `audio_duration`, either:
- **Option A**: Change the queue item to include audio_duration: `(ogg_path, segment_id, audio_duration)`
- **Option B**: Query `SELECT (end_ts - start_ts) FROM segments WHERE id=?`

Option A is simpler — modify `enqueue` to accept `audio_duration` and thread it through.

### 5. Cleanup — `recording/cleanup.py`

In `run_cleanup`, before `db.delete_segments_by_ids(ids)`:

```python
await self.db.delete_perf_logs_by_segment_ids(ids)
```

### 6. Command — `cogs/voicedatabase.py`

```python
@commands.hybrid_command(
    name="perfstats",
    description="Show running averages of processing times per pipeline stage.",
)
@app_commands.describe(days="Lookback window in days (default: 7)")
async def perf_stats(self, context: Context, days: int = 7) -> None:
    days = max(days, 1)
    now = time.time()
    since_ts = now - (days * 86400)
    since_24h = now - 86400

    stats = await self.bot.database.get_perf_stats(since_ts, since_24h)
    if not stats:
        await context.send("No performance data recorded yet.", ephemeral=True)
        return

    embed = discord.Embed(
        title="Performance Stats",
        description=f"Processing time averages per 60s audio segment (last {days} days)",
        color=0x3498DB,
    )

    total = 0.0
    for method, count, avg_60s, avg_24h in stats:
        avg_24h_str = f"{avg_24h:.2f}s" if avg_24h is not None else "—"
        embed.add_field(
            name=method.capitalize(),
            value=f"`{avg_60s:.2f}s` avg ({count} samples) · 24h: `{avg_24h_str}`",
            inline=False,
        )
        total += avg_60s or 0

    embed.set_footer(text=f"Pipeline total: {total:.2f}s per 60s segment")
    await context.send(embed=embed)
```
