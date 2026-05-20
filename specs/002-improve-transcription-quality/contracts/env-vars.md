# Environment Variable Contract: Transcription Quality

**Branch**: `002-improve-transcription-quality` | **Date**: 2026-05-20

All variables are optional. Defaults reproduce existing behaviour for unaffected upgrades.

## Variables

### `WHISPER_MODEL`

| Property | Value |
|----------|-------|
| Type | string |
| Default | `"tiny"` |
| Valid values | `"tiny"`, `"base"`, `"small"`, `"medium"`, `"large-v2"`, `"large-v3"` |
| Effect | Selects the Whisper model checkpoint. Larger = more accurate, slower, more RAM. |
| Recommendation | `"small"` for most self-hosted deployments; `"large-v3"` with `WHISPER_DEVICE=cuda` |

### `WHISPER_DEVICE`

| Property | Value |
|----------|-------|
| Type | string |
| Default | `"cpu"` |
| Valid values | `"cpu"`, `"cuda"` |
| Effect | Runs inference on CPU or NVIDIA GPU. |
| Prerequisite for `"cuda"` | CUDA Toolkit ≥ 11.8, `pip install ctranslate2[cuda]` |

### `WHISPER_COMPUTE_TYPE`

| Property | Value |
|----------|-------|
| Type | string |
| Default | `"int8"` |
| Valid values | `"int8"`, `"int8_float16"`, `"float16"`, `"float32"` |
| Effect | Controls weight quantisation. Higher precision = better accuracy, more memory. |
| Note | `"int8_float16"` requires AVX2. `"float16"` is optimal on CUDA. |

### `WHISPER_BEAM_SIZE`

| Property | Value |
|----------|-------|
| Type | integer |
| Default | `5` |
| Valid range | 1–10 |
| Effect | Number of beams in beam search. Higher = more accurate, slower. |

### `WHISPER_INITIAL_PROMPT`

| Property | Value |
|----------|-------|
| Type | string |
| Default | `""` (disabled) |
| Effect | Pre-seeds the decoder with domain vocabulary to reduce OOV errors. |
| Example | `"Discord voice chat. Players discussing the game."` |

### `WHISPER_VAD_MIN_SILENCE_MS`

| Property | Value |
|----------|-------|
| Type | integer |
| Default | `500` |
| Valid range | 100–2000 |
| Effect | Minimum silence duration (ms) before VAD splits a segment. Higher = fewer cuts mid-word. |

## Upgrade Notes

Operators upgrading from a previous version with no env vars set will get exactly the same
behaviour as before (tiny model, int8, beam_size 3 → **exception**: beam_size default changes
from 3 to 5; set `WHISPER_BEAM_SIZE=3` to preserve old behaviour).
