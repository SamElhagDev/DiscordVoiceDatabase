# Data Model: `//listtext` Command

**Branch**: `001-add-listtext-command` | **Date**: 2026-05-19

## Schema Changes

**None required.** This feature reads from the existing `segments` table using the existing `get_segments_in_range` query. The transcript column already exists.

## Entities Used (read-only)

### Segment (existing)

| Field | Type | Source Index | Used By |
|-------|------|-------------|---------|
| id | INTEGER | 0 | Dropdown value identifier |
| guild_id | INTEGER | 1 | Query filter |
| channel_id | INTEGER | 2 | — |
| user_id | INTEGER | 3 | Query filter |
| start_ts | REAL | 4 | Display formatting (time label) |
| end_ts | REAL | 5 | Display formatting (duration) |
| file_path | TEXT | 6 | — (not used by listtext) |
| file_size | INTEGER | 7 | — (not used by listtext) |
| transcript | TEXT | 8 | **Primary output** — posted as embed |

### State transitions

None — this is a stateless read-only query + display operation.
