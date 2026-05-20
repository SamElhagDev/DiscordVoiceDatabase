# Research: Improve Transcription Quality

**Branch**: `002-improve-transcription-quality` | **Date**: 2026-05-20

## R1: faster-whisper model sizes — accuracy vs. speed tradeoffs

**Decision**: Recommend `small` as the new documented default for most deployments; keep env
var default at `tiny` for zero-regression upgrades.

**Rationale**:
| Model | Params | Relative WER (English) | CPU Transcription speed (1× realtime = 1.0) |
|-------|--------|----------------------|--------------------------------------|
| tiny  | 39M    | baseline (~30% WER)  | ~12× realtime |
| base  | 74M    | ~20% better          | ~6× realtime |
| small | 244M   | ~50% better          | ~2.5× realtime |
| medium| 769M   | ~65% better          | ~0.8× realtime (slower than realtime on weak CPUs) |
| large-v3 | 1550M | ~75% better       | ~0.3× realtime on CPU; needs GPU |

For a Discord bot recording segments (typically 10–120 seconds), `small` runs in 4–50 seconds
on a modern CPU, which is acceptable for background transcription. `medium` is viable on CPUs
with AVX2 support; `large-v3` should only be used with CUDA.

**Alternatives considered**:
- *Upgrade default to `small`*: Would silently increase startup RAM by ~300 MB for existing
  deployments — too surprising. Keeping `tiny` as the env-var default lets operators opt in.
- *Only document model change*: Without exposing `compute_type` and `beam_size`, users still
  get suboptimal accuracy even at `small`.

## R2: compute_type options

**Decision**: Add `WHISPER_COMPUTE_TYPE` env var, default `"int8"`.

**Rationale**:
| compute_type | Memory | Accuracy impact |
|---|---|---|
| int8 | lowest | ~2–3% WER degradation vs float32 |
| int8_float16 | medium | Near-float16 accuracy, requires AVX2 |
| float16 | higher | Matches original Whisper accuracy; best on CUDA |
| float32 | highest | Reference accuracy, slow on CPU |

For CPU-only deployments, `int8_float16` gives a good accuracy bump with manageable RAM. For
CUDA, `float16` is optimal. `int8` remains the default to avoid breaking existing deploys.

**Alternatives considered**:
- *Hardcode `int8_float16`*: Could fail on CPUs without AVX2 (older machines). Env var lets
  operators choose safely.

## R3: beam_size and inference parameters

**Decision**: Change default `beam_size` from `3` to `5`. Expose via `WHISPER_BEAM_SIZE`.
Add `WHISPER_INITIAL_PROMPT` for domain vocabulary seeding.

**Rationale**: Beam search explores more hypothesis paths. `beam_size=5` is OpenAI's original
Whisper default and consistently yields better WER at ~1.5× the inference cost of `beam_size=3`
— a worthwhile tradeoff for background processing. `initial_prompt` guides the decoder to
prefer known vocabulary (usernames, game names, Discord terms), reducing OOV errors. Keep
`temperature=0` (greedy) by default; the VAD filter already handles silence.

**Alternatives considered**:
- *Add temperature fallback*: Whisper uses fallback temperatures when logprob is low. Useful
  for very noisy audio but adds complexity. Deferred.

## R4: CUDA/GPU support on Windows

**Decision**: Add `WHISPER_DEVICE` env var (default `"cpu"`). CUDA support is opt-in and
requires the operator to have installed CUDA drivers + a compatible ctranslate2 build.

**Rationale**: faster-whisper delegates GPU execution to ctranslate2. On Windows, the user
needs CUDA Toolkit 11.x or 12.x and `pip install ctranslate2[cuda]` (or a wheel that
includes CUDA). The Python package itself (`faster-whisper`) already handles CUDA paths when
`device="cuda"` is passed — no code changes beyond threading the env var through. This makes
`large-v3` practical (runs at ~3× realtime on a mid-range GPU vs 0.3× on CPU).

**Alternatives considered**:
- *Add `faster-whisper[cuda]` to requirements.txt*: Would fail on CPU-only environments
  without CUDA libraries. Document it as an optional step instead.

## R5: VAD filter parameter tuning

**Decision**: Expose `WHISPER_VAD_MIN_SILENCE_MS` (default `500`).

**Rationale**: faster-whisper passes `vad_parameters` as a dict to the Silero VAD model.
The `min_silence_duration_ms` parameter controls how much silence triggers a segment split.
The default (300 ms) can cut off the ends of words. Raising it to 500 ms reduces
false-positive silence cuts in natural conversation pauses, at a minor cost to segmentation
accuracy at true sentence boundaries.

**Alternatives considered**:
- *Expose all VAD params*: Over-engineered. Silence duration is the one parameter users
  actually hit.

## R6: Constitution compliance

**Decision**: No principles are violated.

**Rationale**:
- **Principle I (Consent)**: No change to recording logic. Consent checks unaffected.
- **Principle II (Data Integrity)**: Transcription result storage unchanged; only inference
  quality changes. Blank segments still stored as "Blank".
- **Principle III (Accurate Playback)**: Not applicable (no audio path changes).
- **Principle IV (Non-Blocking)**: `_transcribe` still runs in `asyncio.to_thread` — no new
  blocking operations in the event loop.
- **Principle V (User-Facing Clarity)**: No UI changes. Quality improvement is transparent to
  users (better text, same display path).
- **Operational Constraint (Model Loading)**: Remains lazy — `_load_model` only called when
  first segment arrives.
