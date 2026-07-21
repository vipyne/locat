# Progress — Fully-Offline Pipecat Voice Bot (v1)

Shared state for the Ralph build loop. Each iteration reads this, does the first
incomplete task, checks it off with a one-line note, and commits.

## Phase 0 — Scaffold & Python env
- [x] `uv init`; create `.python-version` (3.12) and `pyproject.toml`
- [x] `.gitignore`: `models/`, `.venv/`, `.env`, `__pycache__/`, `*.pyc`
- [x] Verify: `uv run python -c "import sys; print(sys.version)"` reports 3.12.x

## Phase 1 — Dependencies
- [x] Add Pipecat with local-service extras (confirm exact extra names via context hub):
      whisper-mlx, kokoro, ollama, silero, local-smart-turn + pyaudio, python-dotenv,
      loguru, textual
- [x] Verify: `uv sync` succeeds
- [x] Verify: each service class imports cleanly (LocalAudioTransport, WhisperSTTServiceMLX,
      OLLamaLLMService, KokoroTTSService, SileroVADAnalyzer, LocalSmartTurnAnalyzerV3);
      fix deprecated import paths via `check_deprecation`

## Phase 2 — Model acquisition (online step)
- [x] `scripts/run_ollama.sh`: export `OLLAMA_MODELS=$PWD/models/ollama`, `ollama serve` (bg),
      `ollama pull qwen2.5:14b` (tag configurable)
- [x] `scripts/prefetch_models.py`: with `HF_HOME` → `./models/`, force download of Whisper-MLX,
      Kokoro, Silero VAD, Smart Turn v3 model files
- [ ] Verify: `./models/` contains ollama blobs + HF caches + kokoro files; `ollama list` shows
      `qwen2.5:14b` from the repo store

## Phase 3 — The bot pipeline (`bot.py`)
- [ ] Transport: `LocalAudioTransportParams(audio_in/out_enabled, vad_analyzer=SileroVADAnalyzer(),
      turn_analyzer=LocalSmartTurnAnalyzerV3(...))`, device indices from config
- [ ] STT: `WhisperSTTServiceMLX(settings=...(model=MLXModel.<size>))`, configurable
- [ ] LLM: `OLLamaLLMService(model=<env>, base_url=<env>)`
- [ ] TTS: `KokoroTTSService(settings=...(voice=<env>), model_path, voices_path)`
- [ ] Context: current universal LLM context + aggregator (confirm class names via context hub),
      seeded with financial system prompt
- [ ] `PipelineTask(..., params=PipelineParams(allow_interruptions=True))`; `PipelineRunner`
- [ ] Greeting: bot speaks a short opening line on transport ready
- [ ] Verify: pipeline assembles / imports without error (full run is Phase 6)

## Phase 4 — Config & system prompt
- [ ] `config.py`: read env for `LLM_MODEL`, `OLLAMA_BASE_URL`, `WHISPER_MODEL`, `KOKORO_VOICE`,
      `INPUT_DEVICE_INDEX`, `OUTPUT_DEVICE_INDEX`, cache-dir vars; sensible zero-config defaults
- [ ] `.env.example`: documented config knobs
- [ ] `prompts/financial_advisor.py`: private financial thinking-partner system prompt, tuned for
      spoken output (concise, no markdown, not-a-licensed-advisor disclaimer, no real account access)
- [ ] Verify: changing `LLM_MODEL` / `KOKORO_VOICE` in `.env` visibly changes behavior

## Phase 5 — README & example polish (last autonomous step)
- [ ] README: what it is, hardware notes, `brew install portaudio`, `uv sync`, model-pull steps
      with approx sizes, run, run offline, pick audio devices, config table
- [ ] README: explicitly state no API keys required; `.env` is config only, never secrets
- [ ] README: note deferred roadmap (memory → RAG → tools)
- [ ] Verify: README documents clone → `uv sync` → model pull → run → offline run, nothing missing

## Phase 6 — Offline verification (HUMAN-GATED — the success criterion)
- [ ] HUMAN: online smoke test — `uv run bot.py`, speak, hear reply, confirm interruptions
- [ ] HUMAN: offline test — Airplane Mode, local Ollama running, `uv run bot.py`, full conversation
- [ ] HUMAN: confirm zero network calls on the offline run (watch logs)

> Phase 6 is human-gated: Ralph writes the exact steps into `RALPH_BLOCKED.md` and stops when all
> autonomous phases (0–5) are complete.

## Notes log
- (init) PROGRESS.md created from PLAN.md checklist.
- (Phase 0) `uv init --bare` + `uv python pin 3.12`; created `pyproject.toml`
  (`name=locat`) and `.python-version` (3.12). Tightened `requires-python` to
  `>=3.12,<3.13` to prevent uv from resolving 3.14 wheels (plan rules out 3.14 for
  ML wheels). Verify passed: `uv run python` reports 3.12.0; `.venv` created.
  NEXT: Phase 0 `.gitignore` task.
- (Phase 0) Created `.gitignore` (models/, .venv/, .env, __pycache__/, *.pyc).
  Verified with `git check-ignore` — all patterns match. Phase 0 complete.
  NEXT: Phase 1 — add Pipecat deps with local-service extras (confirm exact
  extra names via context hub before `uv add`).
- (Phase 1) Confirmed extra names via context hub: STT MLX = `mlx-whisper`,
  TTS = `kokoro`, LLM = `ollama`, VAD = `silero`. Smart Turn v3 is BUNDLED in
  core pipecat-ai (transformers/onnxruntime included) — NO extra needed (plan's
  guessed `local-smart-turn` does not exist). Surprise: `pipecat.services.whisper.stt`
  imports `faster_whisper` at module load (CPU + MLX classes share one module), so
  the `whisper` extra is ALSO required alongside `mlx-whisper` or the import fails.
  LocalAudioTransport needs `pyaudio` (added directly; PortAudio 19.7.0 already
  brew-installed). Final deps: `pipecat-ai[kokoro,mlx-whisper,ollama,silero,whisper]>=1.5.0`
  + pyaudio, python-dotenv, textual, loguru. `uv sync` clean (123 pkgs, torch 2.13.0);
  all 9 service/pipeline classes import OK on Pipecat 1.5.0 / Python 3.12.0.
  check_deprecation on OLLamaLLMService, LocalSmartTurnAnalyzerV3, WhisperSTTServiceMLX
  → none deprecated. Phase 1 complete.
  NEXT: Phase 2 — model acquisition (online step): `scripts/run_ollama.sh` +
  `scripts/prefetch_models.py`. NOTE: pulling qwen2.5:14b (~9GB) + HF caches needs
  network; if unavailable when reached, this is a legitimate RALPH_BLOCKED (network).
- (Phase 2) Created `scripts/run_ollama.sh`: relocates the Ollama store into the
  repo (`OLLAMA_MODELS=$REPO/models/ollama`), sources `.env` for overrides
  (`LLM_MODEL` default `qwen2.5:14b`, `OLLAMA_HOST` default 127.0.0.1:11434),
  detects an already-running server (warns it may use a different store), otherwise
  starts `ollama serve` in the background and waits up to 30s for readiness via
  `ollama list`, then `ollama pull "$LLM_MODEL"` and keeps the server in the
  foreground (`wait`) if it started one. Decision: gate readiness on `ollama list`
  (not curl) since it's the same tool used everywhere and returns non-zero when no
  server is reachable. Ollama tag == OpenAI-compat model name, so a single
  `LLM_MODEL` drives both this script and the bot.
  CAVEAT (this iteration's env): Bash exec was permission-gated, so I could not run
  `bash -n` (syntax) or `chmod +x`. Script authored to valid POSIX-bash; run it as
  `bash scripts/run_ollama.sh` if the +x bit isn't set. The pull itself (network,
  ~9GB) remains part of the Phase 2 *verify* and is not done yet.
  NEXT: Phase 2 — `scripts/prefetch_models.py` (HF warm-up downloads).
- (Phase 2) Created `scripts/prefetch_models.py`. Confirmed download behavior via
  context hub (NOT guessed):
    * Kokoro `__init__` calls `_ensure_model_files(model_path, voices_path)` →
      downloads `kokoro-v1.0.onnx` + `voices-v1.0.bin`. Script passes repo-local
      paths (`./models/kokoro/...`) so files land where the bot will look.
    * Whisper-MLX `_load` is a NO-OP (downloads lazily on first transcribe into
      HF_HOME). Script resolves the HF repo id from the enum
      (`MLXModel[WHISPER_MODEL].value`, default LARGE_V3_TURBO →
      `mlx-community/whisper-large-v3-turbo`) and `snapshot_download`s it up front.
    * SURPRISE / plan correction: Silero VAD AND Smart Turn v3 are BUNDLED inside
      the pipecat package (`.../audio/vad/data/silero_vad.onnx`,
      `.../audio/turn/smart_turn/data/smart-turn-v3.2-cpu.onnx`), loaded via
      importlib.resources — they DO NOT download anything. (Plan assumed v3 fetches
      from HF; only the deprecated v2 does.) Script still instantiates both as a
      fast offline load-sanity check.
  Env knobs (all optional, .env-overridable): HF_HOME (→ ./models/huggingface),
  WHISPER_MODEL, KOKORO_MODEL_PATH, KOKORO_VOICES_PATH. Sets HF_HOME + kokoro paths
  BEFORE importing any model lib so caches steer into ./models/.
  VERIFY (this task = create the script): `py_compile` clean; ran the offline-safe
  paths with HF_HUB_OFFLINE=1 — MLXModel enum resolves to a valid repo id, and both
  bundled models load from site-packages with zero network. The actual multi-GB
  download run (Kokoro + Whisper) is the SEPARATE Phase 2 verify box below and is
  NOT done yet (needs network + ~1.5GB whisper + ~350MB kokoro).
  NEXT: Phase 2 verify — run `scripts/run_ollama.sh` (pull qwen2.5:14b) +
  `uv run python scripts/prefetch_models.py`; confirm `./models/` is populated and
  `ollama list` shows the model. Network-gated: if unavailable, that's a legit
  RALPH_BLOCKED (network), not a failure.
