# Progress — Fully-Offline Pipecat Voice Bot (v1)

Shared state for the Ralph build loop. Each iteration reads this, does the first
incomplete task, checks it off with a one-line note, and commits.

## Phase 0 — Scaffold & Python env
- [ ] `uv init`; create `.python-version` (3.12) and `pyproject.toml`
- [ ] `.gitignore`: `models/`, `.venv/`, `.env`, `__pycache__/`, `*.pyc`
- [ ] Verify: `uv run python -c "import sys; print(sys.version)"` reports 3.12.x

## Phase 1 — Dependencies
- [ ] Add Pipecat with local-service extras (confirm exact extra names via context hub):
      whisper-mlx, kokoro, ollama, silero, local-smart-turn + pyaudio, python-dotenv,
      loguru, textual
- [ ] Verify: `uv sync` succeeds
- [ ] Verify: each service class imports cleanly (LocalAudioTransport, WhisperSTTServiceMLX,
      OLLamaLLMService, KokoroTTSService, SileroVADAnalyzer, LocalSmartTurnAnalyzerV3);
      fix deprecated import paths via `check_deprecation`

## Phase 2 — Model acquisition (online step)
- [ ] `scripts/run_ollama.sh`: export `OLLAMA_MODELS=$PWD/models/ollama`, `ollama serve` (bg),
      `ollama pull qwen2.5:14b` (tag configurable)
- [ ] `scripts/prefetch_models.py`: with `HF_HOME` → `./models/`, force download of Whisper-MLX,
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
