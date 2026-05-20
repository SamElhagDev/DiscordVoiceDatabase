# Feature Specification: Improve Transcription Quality

**Branch**: `002-improve-transcription-quality` | **Date**: 2026-05-20

## Overview

The current transcription pipeline uses faster-whisper with the `tiny` model, `int8` compute
type, and `beam_size=3`. These defaults prioritise startup speed over accuracy. Users are
seeing transcripts with frequent word errors, especially for names, technical terms, and
overlapping speech. This feature makes all quality-relevant inference parameters configurable
via environment variables and raises the defaults to a better accuracy/speed balance.

## Requirements

### Functional

1. `WHISPER_MODEL` env var already exists; default remains `"tiny"` but documentation MUST
   clearly state that `"small"` or `"medium"` gives materially better accuracy.
2. Add `WHISPER_COMPUTE_TYPE` env var (default `"int8"`) to allow `"int8_float16"`,
   `"float16"`, or `"float32"` for higher-quality inference when RAM allows.
3. Add `WHISPER_DEVICE` env var (default `"cpu"`) to allow `"cuda"` for GPU acceleration,
   enabling larger models to run in real time.
4. Add `WHISPER_BEAM_SIZE` env var (default `5`; current hard-coded value is `3`) — larger
   beam = more accurate hypotheses, small runtime cost on CPU.
5. Add `WHISPER_INITIAL_PROMPT` env var (default empty) — pre-seeds the decoder context with
   domain vocabulary to reduce errors on Discord-specific terms and names.
6. VAD filter parameters: expose `WHISPER_VAD_MIN_SILENCE_MS` (default `500`) to prevent
   aggressive silence cuts that drop the beginnings or ends of utterances.

### Non-Functional

- All new env vars MUST have safe defaults that reproduce the current behaviour if not set
  (so existing deployments are unaffected on upgrade).
- No schema changes.
- No new Python dependencies for the CPU path; CUDA path requires the system to have
  CUDA-capable hardware and the appropriate ctranslate2/CUDA drivers installed separately.
- Changes confined to `recording/transcriber.py` and a `.env.example` update.
- Model loading remains lazy (first use) per the constitution.
