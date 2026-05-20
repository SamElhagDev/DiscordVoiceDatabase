# Data Model: Improve Transcription Quality

**Branch**: `002-improve-transcription-quality` | **Date**: 2026-05-20

## Schema Changes

**None required.** The improvement is entirely in the inference layer. The `segments` table
already stores transcript text; the quality of that text is determined by the Whisper model
parameters, not the schema.

## Entities Affected (read/write — unchanged)

### Segment (existing, no changes)

| Field | Type | Notes |
|-------|------|-------|
| id | INTEGER | Unchanged |
| transcript | TEXT | Written by transcriber; value will be higher quality after this change but type/nullability unchanged |

## Runtime State

### WhisperModel (in-process, not persisted)

| Field | Source | Notes |
|-------|--------|-------|
| model_size | `WHISPER_MODEL` env var | `"tiny"` default |
| device | `WHISPER_DEVICE` env var | `"cpu"` default |
| compute_type | `WHISPER_COMPUTE_TYPE` env var | `"int8"` default |
| beam_size | `WHISPER_BEAM_SIZE` env var (int) | `5` default |
| initial_prompt | `WHISPER_INITIAL_PROMPT` env var | `""` default (no prompt) |
| vad_min_silence_ms | `WHISPER_VAD_MIN_SILENCE_MS` env var (int) | `500` default |

The `WhisperModel` instance is created once on first use (lazy load) and held for the
process lifetime. All parameters are read at load time — changing an env var requires a
bot restart to take effect.

## State Transitions

No new state transitions. The transcription pipeline remains:

```
OGG file → _transcribe() [in thread] → transcript string → db.set_segment_transcript()
```
