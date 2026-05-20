# Command Contract: `//perfstats`

**Branch**: `003-performance-logging` | **Date**: 2026-05-20

## Command

| Property | Value |
|----------|-------|
| Name | `perfstats` |
| Type | hybrid_command (prefix `//` + slash `/perfstats`) |
| Description | Show running averages of processing times per pipeline stage. |

## Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| days | int | No | 7 | Lookback window in days for the averages. |

## Response

Discord embed with:

| Field | Content |
|-------|---------|
| Title | "Performance Stats" |
| Color | Informational blue (0x3498DB) |
| Description | "Processing time averages per 60s audio segment (last {days} days)" |
| Fields (per method) | **{Method}**: `{avg:.2f}s` avg ({count} samples) · 24h: `{avg_24h:.2f}s` |
| Footer | "Pipeline total: {sum_avg:.2f}s per 60s segment" |

### Example output

```
Performance Stats
Processing time averages per 60s audio segment (last 7 days)

Remux        1.23s avg (842 samples) · 24h: 1.19s
Rotate       0.08s avg (842 samples) · 24h: 0.07s
Transcribe   4.56s avg (840 samples) · 24h: 4.42s

Pipeline total: 5.87s per 60s segment
```

### Edge cases

| Condition | Behaviour |
|-----------|-----------|
| No perf_logs data | Ephemeral message: "No performance data recorded yet." |
| A method has no 24h data | Display "—" instead of a number |
| `days` < 1 | Clamp to 1 |
