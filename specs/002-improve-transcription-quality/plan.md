# Implementation Plan: Improve Transcription Quality

**Branch**: `002-improve-transcription-quality` | **Date**: 2026-05-20 | **Spec**: [spec.md](specs/002-improve-transcription-quality/spec.md)
**Input**: Feature specification from `specs/002-improve-transcription-quality/spec.md`

## Summary

Make all faster-whisper quality-relevant inference parameters configurable via environment
variables — device, compute type, beam size, initial prompt, and VAD silence threshold —
so operators can trade speed for accuracy without code changes. The hard-coded `beam_size=3`
default rises to `5`. All changes are confined to `recording/transcriber.py` plus
`.env.example` documentation.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: faster-whisper (already in requirements.txt), ctranslate2 (bundled with faster-whisper; CUDA variant optional)
**Storage**: SQLite via aiosqlite (no schema changes; transcript text quality improves, storage path unchanged)
**Testing**: Manual (bot testing in Discord; transcribe a segment before and after)
**Target Platform**: Windows 11 self-hosted via GitHub Actions Scheduled Task
**Project Type**: Discord bot — background transcription worker
**Performance Goals**: Transcription completes in background; acceptable latency is minutes, not seconds
**Constraints**: CPU-only by default; `WHISPER_DEVICE=cuda` opt-in; model load is lazy (per constitution)
**Scale/Scope**: Single-file change, ~15 lines modified

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Consent-First Recording | PASS | No recording path changes; inference only |
| II. Data Integrity & Retention | PASS | Transcript storage unchanged; "Blank" handling preserved |
| III. Accurate Playback | N/A | No audio playback changes |
| IV. Non-Blocking Architecture | PASS | `_transcribe` remains in `asyncio.to_thread`; no new blocking in event loop |
| V. User-Facing Clarity | PASS | Better transcripts are transparent to users; no UI changes |
| OC: Model loading lazy | PASS | `_load_model` guard pattern unchanged |

**Post-Design Re-Check**: All principles pass. The new constants are read at module load, and
`_load_model` still initialises `self._model` on first call. No blocking paths introduced.

## Project Structure

### Documentation (this feature)

```text
specs/002-improve-transcription-quality/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 findings (model sizes, compute types, params)
├── data-model.md        # No schema changes; runtime state documented
├── quickstart.md        # Step-by-step implementation guide
├── contracts/
│   └── env-vars.md      # Environment variable contract
└── tasks.md             # Phase 2 output (/speckit-tasks — not yet created)
```

### Source Code (repository root)

```text
recording/
└── transcriber.py       # Only file modified — ~15 lines

.env.example             # Add commented-out quality tuning vars (documentation only)
```

**Structure Decision**: Single-file change within the existing transcription module.

## Complexity Tracking

No constitution violations. No complexity escalation needed.

## Implementation Details

### Change 1: Module-level configuration constants

**File**: `recording/transcriber.py`
**Location**: Lines 15–16 (after existing `MODEL_SIZE` constant)

Add five new constants after the existing `MODEL_SIZE` line:

```python
MODEL_SIZE         = os.getenv("WHISPER_MODEL",           "tiny")
DEVICE             = os.getenv("WHISPER_DEVICE",          "cpu")
COMPUTE_TYPE       = os.getenv("WHISPER_COMPUTE_TYPE",    "int8")
BEAM_SIZE          = int(os.getenv("WHISPER_BEAM_SIZE",   "5"))
INITIAL_PROMPT     = os.getenv("WHISPER_INITIAL_PROMPT",  "") or None
VAD_MIN_SILENCE_MS = int(os.getenv("WHISPER_VAD_MIN_SILENCE_MS", "500"))
```

### Change 2: Update `_load_model` to use `DEVICE` and `COMPUTE_TYPE`

**File**: `recording/transcriber.py`
**Location**: `Transcriber._load_model` (lines 25–29)

Replace hardcoded `device="cpu", compute_type="int8"` with the new constants. Update the
log message to include device and compute type for easier debugging.

```python
def _load_model(self):
    if self._model is None:
        logger.info(f"Loading Whisper model: {MODEL_SIZE} device={DEVICE} compute={COMPUTE_TYPE}")
        self._model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
        logger.info("Whisper model loaded")
    return self._model
```

### Change 3: Update `_transcribe` to use `BEAM_SIZE`, `INITIAL_PROMPT`, `VAD_MIN_SILENCE_MS`

**File**: `recording/transcriber.py`
**Location**: `Transcriber._transcribe` (lines 65–74)

Replace the `model.transcribe(...)` call to thread through the three new parameters:

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

### Change 4: `.env.example` documentation

Add a commented-out block documenting all six transcription quality variables with their
defaults and valid values (see `quickstart.md` for the exact block).
