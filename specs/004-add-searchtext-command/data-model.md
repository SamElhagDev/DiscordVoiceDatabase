# Data Model: `//searchtext` Command

## Summary

No schema changes. This feature is read-only and reuses the existing `segments` table.

## Existing Entity: `segments`

The `//searchtext` command reads from the `segments` table using the existing `search_segments_by_transcript` method.

| Field | Type | Used By searchtext |
|-------|------|--------------------|
| id | INTEGER PRIMARY KEY | Yes — segment identifier for dropdown selection |
| guild_id | INTEGER | Yes — filter by guild |
| channel_id | INTEGER | No — not displayed |
| user_id | INTEGER | Yes — filter by user |
| start_ts | REAL | Yes — filter by date range, display timestamp |
| end_ts | REAL | Yes — filter by date range, display duration |
| file_path | TEXT | No — no audio access needed |
| file_size | INTEGER | No — not displayed |
| transcript | TEXT | Yes — searched via LIKE, displayed as output |

## Query Used

```sql
SELECT id, guild_id, channel_id, user_id, start_ts, end_ts, file_path, file_size, transcript
FROM segments
WHERE user_id=? AND guild_id=?
  AND start_ts <= ? AND (end_ts >= ? OR end_ts IS NULL)
  AND transcript IS NOT NULL AND transcript != '' AND transcript != 'Blank'
  AND transcript LIKE ? COLLATE NOCASE
ORDER BY start_ts ASC
```

No new queries, indexes, or migrations required.
