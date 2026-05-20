# Quickstart: Implement Transcription Quality Improvements

**Branch**: `002-improve-transcription-quality` | **Date**: 2026-05-20

## The One File to Change

All changes live in `recording/transcriber.py`. No other files need modification except
`.env.example` (documentation only).

## Change Summary

Replace the five hardcoded constants/parameters with env-var-driven configuration:

| What | Before | After |
|------|--------|-------|
| Model size | `os.getenv("WHISPER_MODEL", "tiny")` | unchanged (already env-var) |
| Device | hardcoded `"cpu"` | `os.getenv("WHISPER_DEVICE", "cpu")` |
| Compute type | hardcoded `"int8"` | `os.getenv("WHISPER_COMPUTE_TYPE", "int8")` |
| Beam size | hardcoded `3` | `int(os.getenv("WHISPER_BEAM_SIZE", "5"))` |
| Initial prompt | absent | `os.getenv("WHISPER_INITIAL_PROMPT", "") or None` |
| VAD min silence | absent | `int(os.getenv("WHISPER_VAD_MIN_SILENCE_MS", "500"))` |

## Implementation Steps

### Step 1 — Update module-level constants (`recording/transcriber.py`, top of file)

```python
MODEL_SIZE    = os.getenv("WHISPER_MODEL",          "tiny")
DEVICE        = os.getenv("WHISPER_DEVICE",         "cpu")
COMPUTE_TYPE  = os.getenv("WHISPER_COMPUTE_TYPE",   "int8")
BEAM_SIZE     = int(os.getenv("WHISPER_BEAM_SIZE",  "5"))
INITIAL_PROMPT = os.getenv("WHISPER_INITIAL_PROMPT", "") or None
VAD_MIN_SILENCE_MS = int(os.getenv("WHISPER_VAD_MIN_SILENCE_MS", "500"))
```

### Step 2 — Update `_load_model` to use new constants

```python
def _load_model(self):
    if self._model is None:
        logger.info(f"Loading Whisper model: {MODEL_SIZE} device={DEVICE} compute={COMPUTE_TYPE}")
        self._model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
        logger.info("Whisper model loaded")
    return self._model
```

### Step 3 — Update `_transcribe` to use new parameters

```python
def _transcribe(self, audio_path: str) -> str:
    model = self._load_model()
    segments, _ = model.transcribe(
        audio_path,
        language="en",
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": VAD_MIN_SILENCE_MS},
        beam_size=BEAM_SIZE,
        initial_prompt=INITIAL_PROMPT,
    )
    text = " ".join(seg.text.strip() for seg in segments)
    return text.strip()
```

## Testing

1. Start the bot with default env vars — verify transcriptions still work (no regression).
2. Set `WHISPER_MODEL=small` and `WHISPER_BEAM_SIZE=5` in `.env`, restart, and transcribe
   a segment. Compare transcript quality to previous output.
3. Optional: set `WHISPER_INITIAL_PROMPT="Discord voice chat"` and re-transcribe — check
   whether Discord-specific terms are spelled correctly.

## `.env.example` Addition

```env
# Transcription quality tuning
# WHISPER_MODEL=small           # tiny|base|small|medium|large-v3 (default: tiny)
# WHISPER_DEVICE=cpu            # cpu|cuda (default: cpu)
# WHISPER_COMPUTE_TYPE=int8     # int8|int8_float16|float16|float32 (default: int8)
# WHISPER_BEAM_SIZE=5           # 1-10 (default: 5)
# WHISPER_INITIAL_PROMPT=       # e.g. "Discord voice chat." (default: empty)
# WHISPER_VAD_MIN_SILENCE_MS=500  # 100-2000 (default: 500)
```
