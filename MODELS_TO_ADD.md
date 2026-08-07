# Models / engines to consider adding later

Parking lot for STT/TTS engines that don't (yet) have a clean path into
`services.py`. Context: engines live in `services.py` behind `STT_ENGINE` /
`TTS_ENGINE`; anything here is *not* selectable yet and why.

_Last reviewed: 2026-08-07._

## Pocket TTS (Kyutai) — the one to watch

- 100M-param CPU TTS: MIT code, ~200 ms latency, ~6× real-time on CPU,
  zero-shot voice cloning from a wav, 6 languages.
  https://github.com/kyutai-labs/pocket-tts
- Arguably the best ideological fit for locat of any TTS engine (tiny, fast,
  fully local, MIT vs Piper's GPL-3.0) — **but Pipecat has no built-in service**
  (open request: https://github.com/pipecat-ai/pipecat/issues/3487).

Options, in rough order of preference:

1. **In-process custom `PocketTTSService`** — small Pipecat `TTSService`
   subclass around the pip-installable `pocket-tts` package, wired as
   `TTS_ENGINE=pocket`. Matches the existing in-process pattern (Piper works
   this way). Precedent: kwindla's demo agent used exactly this approach
   (https://x.com/kwindla/status/2050275653501776064). Retire it when Pipecat
   ships official support.
2. **Via vr000m's TTS server** (below) — heavier, buys more engines.
3. **Wait** for Pipecat to land #3487, then it's just another `services.py`
   branch + doctor catalog rows.

## vr000m local STT/TTS WebSocket servers

Standalone MLX-based (Apple Silicon) WebSocket servers + reference Pipecat
adapters, BSD-2. The Ollama pattern applied to STT/TTS: background server
process, models stay warm across bot restarts, steady-paced audio streaming
(prevents playback buffer starvation).

- TTS: https://github.com/vr000m/pipecat-local-tts-server —
  backends: Kokoro-via-MLX (GPU, vs locat's ONNX/CPU), **Pocket TTS**,
  Qwen3-TTS, Voxtral (CC-BY-NC — non-commercial), dia (multi-speaker).
- STT: https://github.com/vr000m/pipecat-local-stt-server —
  backends: MLX Whisper, **Parakeet** (`parakeet-tdt-0.6b`), **Nemotron**
  streaming ASR — modern fast MLX ASR families Pipecat has no service for.

Caveats found on review (2026-08):

- The reference adapter (`examples/pipecat_tts_service.py`, ~550 lines) **pins
  pipecat-ai 1.4.0 and overrides the private `_sample_rate` field**; locat runs
  1.6.0, so using it means vendoring + porting (Settings API, sample-rate
  handling) and maintaining it here.
- Single-maintainer repos — API drift is on us to track.
- Integration shape: `scripts/run_tts_server.sh` (+ start.sh management, like
  Ollama), vendored adapter, `TTS_ENGINE=local_server` branch in services.py,
  doctor catalog rows + combo logic.

Choose the servers over option 1 above if we also want Parakeet/Nemotron STT
and warm-model restarts — one ported adapter per side buys all their backends.

## XTTS (Coqui) — deliberately excluded

Considered and rejected when Piper was wired in (2026-08): ~2 GB, slow on CPU
(poor real-time voice fit), needs a separately managed XTTS server, and Coqui
shut down in early 2024 so upstream is unmaintained. Its voice-cloning niche is
covered better and lighter by Pocket TTS. Don't re-add without a new reason.

## Adding an engine: the checklist

When any of the above graduates, the wiring is mechanical — mirror the Piper
integration (added 2026-08):

1. `services.py`: new branch in `build_stt()`/`build_tts()` (lazy import,
   actionable error if the dep is missing).
2. `config.py` + `.env.example`: engine value + model/voice var + defaults.
3. `pyproject.toml`: optional extra if it needs a new package.
4. `doctor.sh`: catalog rows/group (sizes, language notes, fit verdicts),
   `-i` picker resolution, gated `uv sync --extra` install, combo math.
5. `scripts/print_models.py`: engine-aware STT/TTS line.
6. README: component table + config table + layout.
7. Verify: construct the service via `uv run python -c`, doctor expect-tests,
   `./start.sh` smoke.
