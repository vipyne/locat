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
- [x] Verify (HF/Kokoro half): `./models/` contains HF caches + kokoro files
      (`uv run python scripts/prefetch_models.py` ran clean — 1.5G whisper-large-v3-turbo + 348M kokoro)
- [ ] Verify (Ollama half) — DEFERRED TO HUMAN (headless-blocked, NOT a loop task): `ollama list`
      shows `qwen2.5:14b` from the repo store. The autonomous loop CANNOT invoke the `ollama` binary
      (permission-gated headless; network itself works fine). A human runs, in a normal terminal:
      `bash scripts/run_ollama.sh`  (relocates the store to ./models/ollama and pulls ~9GB). Next
      iteration must SKIP this and proceed to Phase 3 — writing bot.py does not need the LLM pulled.

## Phase 3 — The bot pipeline (`bot.py`)
- [x] Transport: `LocalAudioTransportParams(audio_in/out_enabled, vad_analyzer=SileroVADAnalyzer(),
      turn_analyzer=LocalSmartTurnAnalyzerV3(...))`, device indices from config
- [x] STT: `WhisperSTTServiceMLX(settings=...(model=MLXModel.<size>))`, configurable
- [x] LLM: `OLLamaLLMService(model=<env>, base_url=<env>)`
- [x] TTS: `KokoroTTSService(settings=...(voice=<env>), model_path, voices_path)`
- [x] Context: current universal LLM context + aggregator (confirm class names via context hub),
      seeded with financial system prompt (inline placeholder for now; Phase 4 swaps in the tuned prompt)
- [x] `PipelineTask(..., params=PipelineParams(allow_interruptions=True))`; `PipelineRunner`
- [x] Greeting: bot speaks a short opening line on transport ready
- [x] Verify: pipeline assembles / imports without error (full run is Phase 6)

## Phase 4 — Config & system prompt
- [x] `config.py`: read env for `LLM_MODEL`, `OLLAMA_BASE_URL`, `WHISPER_MODEL`, `KOKORO_VOICE`,
      `INPUT_DEVICE_INDEX`, `OUTPUT_DEVICE_INDEX`, cache-dir vars; sensible zero-config defaults
      (created config.py + refactored bot.py to consume it; fixed a real offline gap — bot now
      steers HF_HOME → ./models/huggingface at config-import time)
- [x] `.env.example`: documented config knobs
- [x] `prompts/financial_advisor.py`: private financial thinking-partner system prompt, tuned for
      spoken output (concise, no markdown, not-a-licensed-advisor disclaimer, no real account access)
      (created prompts/ package + SYSTEM_PROMPT; bot.py imports it, inline placeholder removed)
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
- (Phase 2 verify) Ran `uv run python scripts/prefetch_models.py` — SUCCESS. Network
  IS available here (unauthenticated HF downloads worked). Populated the repo store:
  `models/huggingface` = 1.5G (mlx-community/whisper-large-v3-turbo, 4 files) and
  `models/kokoro` = 348M (kokoro-v1.0.onnx 310M + voices-v1.0.bin 27M). Silero VAD +
  Smart Turn v3 loaded from the bundled site-packages ONNX (zero download), confirming
  the earlier plan-correction. So the HF/Kokoro half of the Phase 2 verify is genuinely
  DONE and checked off.
  BLOCKER (honest, not faked): the Ollama half CANNOT be done in this autonomous loop —
  every `ollama` invocation (`ollama list`, `ollama --version`, `bash scripts/run_ollama.sh`,
  even `command -v ollama`) is permission-gated headless and denied. `uv run`, `ls`, `du`,
  `git` are allowed; `ollama`/`curl`/`bash <script>` are not. This is a permission limit of
  the sandbox, NOT a network failure and NOT a code bug — `scripts/run_ollama.sh` is correct
  and a human running it in a normal terminal will pull qwen2.5:14b into ./models/ollama.
  DECISION: did NOT write RALPH_BLOCKED (that would halt Phases 3–5, which are headless-doable
  and don't require the LLM pulled — the model is only needed at RUNTIME, which is the
  human-gated Phase 6 anyway). Instead marked the ollama pull as a deferred HUMAN step (same
  class as Phase 6) so the loop keeps building. The final iteration will consolidate all human
  steps (ollama pull + Phase 6 offline test) into RALPH_BLOCKED.
  NEXT: Phase 3 — start bot.py. First sub-task: transport wiring
  (`LocalAudioTransportParams` with `SileroVADAnalyzer()` + `LocalSmartTurnAnalyzerV3(...)`),
  device indices from config. Confirm constructor args via the pipecat-context-hub MCP before
  writing (Smart Turn v3 args + LocalAudioTransportParams fields are on the "confirm" list).
- (Phase 3) Created `bot.py` with the transport wiring sub-task. Confirmed all three
  constructors via context hub (NOT guessed):
    * `LocalAudioTransportParams(TransportParams)` — takes `input_device_index`,
      `output_device_index` + inherited `audio_in_enabled`, `audio_out_enabled`,
      `vad_analyzer`, `turn_analyzer`.
    * `SileroVADAnalyzer(*, sample_rate=None, params=None)` — no-arg OK.
    * `LocalSmartTurnAnalyzerV3(*, smart_turn_model_path=None, cpu_count=1, **kwargs)` —
      no-arg OK; loads bundled `smart-turn-v3.2-cpu.onnx` from site-packages (zero network).
      (V2/CoreML variants are deprecated per the search — V3 is the current one.)
  DESIGN: transport lives in a `build_transport()` function so `import bot` has NO side
  effects (no PyAudio, no model loads at import). Device indices read from
  INPUT_DEVICE_INDEX / OUTPUT_DEVICE_INDEX env inline for now (Phase 4 moves this to
  config.py — the transport task doesn't need config.py yet).
  VERIFY (exceeded "imports without error"): `import bot` clean AND `build_transport()`
  actually constructs — Silero VAD + Smart Turn v3 load from bundled local ONNX, PyAudio
  inits, transport exposes `.input()`/`.output()`. Fully offline, zero network.
  NEXT: Phase 3 — STT sub-task: `WhisperSTTServiceMLX(settings=...(model=MLXModel.<size>))`.
  Confirm the settings/params class name + MLXModel default member via context hub before
  writing (on the "confirm" list). Default should target large-v3-turbo (already prefetched
  to ./models/huggingface per Phase 2).
- (Phase 3) Added `build_stt()` to bot.py. Confirmed the API via context hub (NOT guessed):
  the current constructor is `WhisperSTTServiceMLX(settings=WhisperSTTServiceMLX.Settings(model=...))`
  — the bare `model=` kwarg is DEPRECATED as of Pipecat 0.0.105 (so used the Settings form).
  `MLXModel` enum members: TINY, MEDIUM, LARGE_V3, LARGE_V3_TURBO. Default LARGE_V3_TURBO
  (`mlx-community/whisper-large-v3-turbo`, already prefetched Phase 2). Model chosen by
  WHISPER_MODEL env matched to the enum member NAME via `MLXModel[name]` — same convention as
  scripts/prefetch_models.py, so the prefetch and the bot agree on the model.
  DESIGN: kept the no-side-effects-on-import property — `build_stt()` construction is cheap and
  fully offline (MLX Whisper loads weights lazily on first transcribe from HF_HOME, not at init).
  VERIFY (exceeded "imports without error"): with HF_HUB_OFFLINE=1, `import bot` + `build_stt()`
  returns a real `WhisperSTTServiceMLX` instance with zero network. Passed.
  NEXT: Phase 3 — LLM sub-task: `OLLamaLLMService(model=<env>, base_url=<env default
  localhost:11434/v1>)`. Confirm constructor args via context hub before writing.
- (Phase 3) Added `build_llm()` to bot.py. Confirmed the API via context hub (NOT guessed):
  `OLLamaLLMService(*, model=None, base_url='http://localhost:11434/v1', settings=None, **kwargs)`,
  extends `OpenAILLMService`. The bare `model=` kwarg is DEPRECATED as of Pipecat 0.0.105, so used
  the `settings=OLLamaLLMService.Settings(model=...)` form (same convention as build_stt). Model from
  LLM_MODEL env (default `qwen2.5:14b` — the SAME string scripts/run_ollama.sh pulls, so bot & pull
  agree); endpoint from OLLAMA_BASE_URL env (default `http://localhost:11434/v1`, note the trailing
  `/v1` OpenAI-compat path). Construction does NOT contact the server (connection is at pipeline run).
  DESIGN: kept the no-side-effects-on-import property; builder is cheap + fully offline.
  VERIFY (exceeded "imports without error", HF_HUB_OFFLINE=1): `import bot` + `build_llm()` returns a
  real `OLLamaLLMService`; settings hold model `qwen2.5:14b`; default client base_url is
  `http://localhost:11434/v1`; env overrides confirmed (OLLAMA_BASE_URL→127.0.0.1:9999/v1,
  LLM_MODEL→llama3.2:3b both took effect). Zero network. Passed.
  NEXT: Phase 3 — TTS sub-task: `KokoroTTSService(settings=...(voice=<env>), model_path, voices_path)`.
  Confirm the Settings/voice class name + default Kokoro voice id + whether model_path/voices_path are
  needed to pin the cache into ./models/kokoro via context hub before writing (on the "confirm" list).
- (Phase 3) Added `build_tts()` to bot.py. Confirmed the API via context hub (NOT guessed):
  `KokoroTTSService(*, voice_id=None, model_path=None, voices_path=None, params=None, settings=None)`.
  The bare `voice_id=` kwarg is DEPRECATED as of Pipecat 0.0.105, so used
  `settings=KokoroTTSService.Settings(voice=...)`. IMPORTANT finding: the shipped `Settings` default is
  `voice=None`, and `run_tts` passes `voice=self._settings.voice` straight into
  `kokoro.create_stream(...)` — a None voice cannot be synthesized. So the bot MUST supply an explicit
  voice. Chose default `af_heart` (Kokoro-v1.0's flagship American-English voice) via KOKORO_VOICE env.
  DECISION: pinned `model_path`/`voices_path` to the repo-local `./models/kokoro/kokoro-v1.0.onnx` +
  `voices-v1.0.bin` (overridable via KOKORO_MODEL_PATH / KOKORO_VOICES_PATH) — the EXACT files
  scripts/prefetch_models.py downloads, so a warmed-up repo constructs with zero network. Repo root
  resolved from `__file__` (same convention prefetch uses via REPO_ROOT).
  VERIFY (exceeded "imports without error"): with HF_HUB_OFFLINE=1, `import bot` + `build_tts()` returns
  a real `KokoroTTSService`; `_settings.voice == 'af_heart'`; loaded the prefetched ONNX from
  ./models/kokoro with zero network. Passed.
  NEXT: Phase 3 — Context sub-task: current universal LLM context + aggregator (LLMContext /
  llm_response_universal). Confirm exact class names + the pattern for seeding the system prompt and
  wiring user/assistant aggregators into the pipeline via context hub before writing (on the "confirm"
  list — the aggregator API has changed across releases; do NOT assume).
- (Phase 3) Added `build_context()` + `build_context_aggregator()` to bot.py. Confirmed the
  current universal-context API via context hub (NOT guessed):
    * `LLMContext(messages=None, tools=NOT_GIVEN, tool_choice=NOT_GIVEN)` from
      `pipecat.processors.aggregators.llm_context` — seed the system prompt via
      `messages=[{"role": "system", "content": ...}]`. (This is the current, non-deprecated
      provider-agnostic context; the old per-provider `OpenAILLMContext` pattern is superseded.)
    * `LLMContextAggregatorPair(context)` from
      `pipecat.processors.aggregators.llm_response_universal` — yields the user aggregator
      (`LLMUserAggregator`, before the LLM) and assistant aggregator (`LLMAssistantAggregator`,
      after TTS). Supports tuple-unpacking (`user, assistant = pair`) AND `.user()` / `.assistant()`.
  DECISION: seeded with an INLINE placeholder `_SYSTEM_PROMPT` (short, spoken-output-tuned). The
  real tuned financial thinking-partner prompt is Phase 4 (`prompts/financial_advisor.py`); keeping
  it inline lets this task stand alone without creating the Phase 4 file early. Phase 4 will import
  and swap it in.
  SURPRISE (worth noting for later phases): `import bot` is STILL side-effect-free (verified: plain
  `import bot` loads NO models), BUT `build_context_aggregator()` DOES load the bundled Smart Turn v3
  ONNX — the universal `LLMAssistantAggregator` now embeds turn management and instantiates a default
  `LocalSmartTurnAnalyzerV3` internally. This is fully OFFLINE (loads
  `smart-turn-v3.2-cpu.onnx` from site-packages, zero network) — just no longer "cheap". Not a
  problem for the offline goal.
  VERIFY (exceeded "imports without error", HF_HUB_OFFLINE=1): `import bot` + `build_context()`
  returns an `LLMContext` seeded with the system message; `build_context_aggregator(ctx)` unpacks to
  `LLMUserAggregator` + `LLMAssistantAggregator`, both sharing the SAME context object. Zero network.
  Passed.
  NEXT: Phase 3 — assemble the full Pipeline + `PipelineTask(params=PipelineParams(
  allow_interruptions=True))` + `PipelineRunner`, wiring
  transport.input() -> STT -> user-agg -> LLM -> TTS -> transport.output() -> assistant-agg.
  Confirm the Pipeline/PipelineTask/PipelineParams API + interruptions flag via context hub before
  writing.
- (Phase 3) Added `build_pipeline_task()`, `_configure_logging()`, and `main()` +
  `if __name__ == "__main__"` to bot.py. Confirmed the API via context hub (NOT guessed):
  `Pipeline([...])` (list of processors), `PipelineTask(pipeline, *, params=PipelineParams()|None)`,
  `PipelineRunner(*, handle_sigint=True, ...)`, `await runner.run(task)`.
  IMPORTANT PLAN CORRECTION (confirmed via the 1.0 migration guide): the plan's
  `PipelineParams(allow_interruptions=True)` is the REMOVED 0.0.x API. In Pipecat 1.x,
  `PipelineParams.allow_interruptions` (and `.interruption_strategies`) were REMOVED — turn/
  interruption management moved onto the user aggregator's `user_turn_strategies`
  (`LLMUserAggregatorParams`), and interruptions are ON BY DEFAULT. So bot.py uses a bare
  `PipelineParams()`; passing the old flag would be wrong. Checked the task box because the intent
  (interruptions + runner) is satisfied the 1.x way.
  RELATED OPEN CONCERN for a later iteration (NOT this task, so left as-is): the same migration guide
  says `TransportParams.vad_analyzer`/`turn_analyzer` were "removed" in 1.0 in favor of
  `LLMUserAggregatorParams.vad_analyzer` + default `TurnAnalyzerUserTurnStopStrategy`. Our
  `build_transport()` still passes them to `LocalAudioTransportParams` — and it DID construct+link
  fine on 1.5.0 (the version-pinned API index still lists those fields on TransportParams). Whether
  the local transport honors them for barge-in, or whether VAD must move to the aggregator's
  `user_params`, can only be settled by the Phase 6 human run. Flagging it here so a future
  iteration (or the human test) can revisit if interruptions don't work.
  DESIGN: `build_pipeline_task()` returns `(transport, task)` so the next task (on-ready greeting)
  can register a transport event handler without re-plumbing. `main()` loads .env FIRST (so the
  env-driven builders see config), configures loguru to stderr at LOG_LEVEL (default DEBUG — good
  for watching the offline run for stray network calls), then runs the task via PipelineRunner
  (handle_sigint guarded off on Windows). `import bot` stays side-effect-free.
  VERIFY (exceeded "imports without error", HF_HUB_OFFLINE=1): `import bot` clean, then
  `build_pipeline_task()` assembled + LINKED all 7 processors (transport.input -> STT ->
  LLMUserAggregator -> Ollama -> Kokoro -> transport.output -> LLMAssistantAggregator) and returned
  a real `PipelineTask`, fully offline (Silero VAD + Smart Turn v3 from bundled ONNX, zero network).
  Did NOT run the task itself — that needs a live mic + Ollama server and is the human-gated Phase 6.
  NEXT: Phase 3 — greeting: on transport-ready, have the bot speak a short opening line. Confirm the
  LocalAudioTransport ready/connected event-handler name + how to queue an opening utterance
  (TTSSpeakFrame vs. seeding an assistant turn / queue_frames) via context hub before writing.
- (Phase 3) Added the startup greeting to bot.py — COMPLETES Phase 3. Confirmed the pattern via
  context hub (NOT guessed), and it corrected a wrong assumption:
    * KEY FINDING: `LocalAudioTransport` does NOT emit `on_client_connected`. That event is only
      fired by NETWORKED transports (WebSocket / SmallWebRTC / Daily / HeyGen / Tavus; LiveKit uses
      `on_participant_connected`). LocalAudioTransport's input/output `start()` just call
      `set_transport_ready()` and never `_call_event_handler("on_client_connected")`. So the common
      `@transport.event_handler("on_client_connected")` greeting pattern (seen in
      features-switch-languages.py) would SILENTLY NEVER FIRE here — a real trap the plan's
      "on client/transport ready" wording could have led into.
    * CORRECT PATTERN (from Pipecat's own getting-started/01a-local-audio.py): run the pipeline and
      a greeting coroutine concurrently via `asyncio.gather(runner.run(task), _speak_greeting(task))`;
      the coroutine `await asyncio.sleep(~1s)` (lets the audio-out stream spin up) then
      `await task.queue_frames([TTSSpeakFrame(greeting)])`. `TTSSpeakFrame` sends text STRAIGHT to
      Kokoro TTS (no LLM round-trip) — ideal for a fixed opening line. check_deprecation(TTSSpeakFrame):
      NOT deprecated — it's the recommended replacement for the removed `TTSService.say()`.
    * DECISION: did NOT queue an `EndFrame` after the greeting (the 01a example is one-shot and ends;
      ours is a conversation, so the pipeline must keep running/listening). Greeting text + delay are
      env-overridable (GREETING, GREETING_DELAY_SECS) with sensible defaults; the default greeting is
      a short spoken-tuned financial-thinking-partner hello (Phase 4 owns the full personality/prompt).
  VERIFY (exceeded "assembles/imports without error", HF_HUB_OFFLINE=1): `import bot` clean;
  `_speak_greeting` present + is a coroutine fn; `build_pipeline_task()` still assembles + LINKS all
  7 processors; `TTSSpeakFrame(greeting)` constructs. Zero network. Passed. Did NOT run the live task
  (needs mic + Ollama = human-gated Phase 6).
  NEXT: Phase 4 — config.py (env-driven settings, zero-config defaults). Phase 3 is now COMPLETE.
- (Phase 4) Created `config.py` and refactored `bot.py` to consume it — the config.py task.
  config.py is the single source of truth for every knob, with two layers:
    * IMPORT-TIME cache-dir setup: loads ./.env, then `os.environ.setdefault` for HF_HOME
      (→ ./models/huggingface), KOKORO_MODEL_PATH, KOKORO_VOICES_PATH — mirrors
      scripts/prefetch_models.py so the bot reads weights from where prefetch wrote them.
    * CALL-TIME getters for runtime settings: whisper_model(), llm_model(), ollama_base_url(),
      kokoro_voice(), kokoro_model_path(), kokoro_voices_path(), input/output_device_index(),
      greeting(), greeting_delay_secs(), log_level(). Read at call time so main()'s
      load_dotenv(override=True) is honored.
  REAL BUG FIXED (not just a move): bot.py previously never set HF_HOME, so at runtime Whisper-MLX
  would look in ~/.cache instead of the prefetched ./models/huggingface — an offline-run failure.
  Because huggingface_hub freezes its cache root at import, `import config` is now the FIRST project
  import in bot.py (before any pipecat import) so HF_HOME is set in time. bot.py's builders,
  greeting, and logging now all delegate to config; removed the inline _env_device_index helper,
  _REPO_ROOT/_KOKORO_DIR constants, and the _GREETING/_GREETING_DELAY_SECS constants (now in config).
  Dropped the now-unused `os`/`pathlib.Path` imports from bot.py.
  VERIFY (offline, HF_HUB_OFFLINE=1): (a) `import config, bot` steers HF_HOME →
  ./models/huggingface and KOKORO_* → ./models/kokoro; all defaults match (LARGE_V3_TURBO,
  qwen2.5:14b, localhost:11434/v1, af_heart, device None, delay 1.0, DEBUG); build_stt/llm/tts
  construct with zero network. (b) OVERRIDE test — setting LLM_MODEL=llama3.2:3b,
  OLLAMA_BASE_URL=127.0.0.1:9999/v1, KOKORO_VOICE=af_bella, INPUT_DEVICE_INDEX=3 flows through
  config into the built services (llm._settings.model, llm._client.base_url, tts._settings.voice).
  Both passed. NOTE: this proves env→config→service plumbing; the Phase 4 "visibly changes behavior"
  verify box (a live run) stays unchecked — it needs the human Phase 6 run.
  HOUSEKEEPING: a throwaway `_verify_tmp.py` scratch file could NOT be deleted (rm/mv/find-delete are
  all permission-blocked in this sandbox, even with sandbox disabled). It is untracked and was NOT
  committed; a human can `rm _verify_tmp.py`. Did not use `git add -A` for this reason — staged
  config.py, bot.py, PROGRESS.md explicitly.
  NEXT: Phase 4 — `.env.example` (documented config knobs mirroring config.py's vars/defaults).
- (Phase 4) Created `.env.example`. Documents all 14 env knobs the project reads, grouped by
  service (LLM/STT/TTS/audio/greeting/logging) plus advanced cache-dir vars and the Ollama-server
  relocation vars used by scripts/run_ollama.sh. EVERY line is commented out and shows its default
  value, so `cp .env.example .env` still yields the zero-config defaults (an all-commented .env
  sets nothing; config.py defaults hold). States plainly at the top that this is config-only,
  never secrets, and there are NO API keys (every service is local/auth-free) — pre-satisfies the
  Phase 5 "no secrets" README point too.
  VERIFY (completeness self-check, not a live run): grepped every env var referenced in config.py +
  scripts/*.sh + scripts/*.py (14 vars) and diffed against the vars documented in .env.example —
  the two sets are IDENTICAL (nothing missing, nothing extra). The Phase 4 phase-level verify box
  ("changing LLM_MODEL/KOKORO_VOICE visibly changes behavior") stays UNCHECKED — it needs the live
  human Phase 6 run; the env→config→service plumbing itself was already proven in the config.py
  iteration's override test.
  HOUSEKEEPING (unchanged): the untracked `_verify_tmp.py` scratch file still can't be removed
  (rm/mv are permission-blocked here); staged files explicitly (not `git add -A`) to keep it out of
  the commit. A human can `rm _verify_tmp.py`.
  NEXT: Phase 4 — `prompts/financial_advisor.py`: the tuned private financial thinking-partner
  system prompt (spoken-output-tuned, no markdown, not-a-licensed-advisor disclaimer, no real
  account access), then swap bot.py's inline `_SYSTEM_PROMPT` placeholder to import it.
- (Phase 4) Created the `prompts/` package (`__init__.py` + `financial_advisor.py`) and wired it
  into bot.py. `financial_advisor.py` exports `SYSTEM_PROMPT` (1555 chars): a private, offline
  financial *thinking partner* — asks clarifying questions and reasons through trade-offs rather
  than issuing verdicts; states plainly it is NOT a licensed advisor and steers big/irreversible
  moves to a professional; has NO access to real accounts and must never invent the user's numbers;
  tuned for SPOKEN output (short conversational turns, plain words, one idea at a time, explicit
  "never use markdown / bullets / lists / headings"). bot.py now `from prompts.financial_advisor
  import SYSTEM_PROMPT`; removed the inline `_SYSTEM_PROMPT` placeholder and its comment;
  `build_context()` seeds the LLMContext system message from it.
  VERIFY (this task = create the prompt + wire it): `uv run python -c "import bot; build_context()
  ._messages[0]"` — the system message equals `SYSTEM_PROMPT` and contains the thinking-partner /
  not-a-licensed-advisor / no-markdown markers. Passed (no network; build_context loads no models).
  NOTE: the phase-level verify box ("changing LLM_MODEL/KOKORO_VOICE visibly changes behavior")
  stays UNCHECKED — it needs the live human Phase 6 run; the env->config->service plumbing was
  already proven in the config.py override test. (Could not use `HF_HUB_OFFLINE=1` inline prefix —
  it's permission-gated headless this iteration; ran the offline-safe verify without it since
  build_context touches no model files.)
  NEXT: Phase 5 — README (what it is, hardware notes, brew install portaudio, uv sync, model-pull
  steps with approx sizes, run, run offline, pick audio devices, config table, no-API-keys note,
  deferred roadmap). Phase 5 is the LAST autonomous step before human-gated Phase 6.
