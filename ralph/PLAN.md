# Fully-Offline Pipecat Voice Bot — v1 (Conversation Only)

## Context

**Goal:** Build a Pipecat voice bot that runs 100% offline — STT, LLM, and TTS are all local
services, and the transport is the machine's own audio hardware (mic + speakers). The success
test: turn off Wi-Fi, disconnect from the network, start the bot, and have a spoken conversation.

**Why:** Two purposes.
1. **Public example** — this repo will be published to show people how to build a fully-local
   Pipecat bot using the latest Pipecat release. Clarity and reproducibility matter as much as
   function.
2. **Personal use** — the author wants to talk through *financial decisions*. Because those
   details are personal and sensitive, no cloud LLM API is acceptable; the model must be local.

**Scope of THIS plan (v1):** Conversation only. A well-crafted financial-thinking-partner system
prompt, spoken back and forth. **No** persistent memory, **no** document RAG, **no** function
calling/tools — those are explicitly deferred to later phases. The repo is structured so they
layer in cleanly, but v1 is the smallest thing that fully works end-to-end offline.

**Decisions locked in:**
| Component | Choice | Notes |
|---|---|---|
| STT | `WhisperSTTServiceMLX` | Apple-Silicon-optimized Whisper via MLX |
| LLM | Qwen2.5-14B-Instruct (Q4) via **Ollama** | Balanced smarts/latency; env-configurable |
| TTS | `KokoroTTSService` | Natural local neural voice (kokoro-onnx) |
| Turn-taking | Silero VAD + Local Smart Turn v3 | Barge-in / interruptions, fully local |
| Transport | `LocalAudioTransport` | PyAudio mic+speaker I/O |
| Runtime | Python **3.12** (pinned), `uv` | 3.14 is too new for ML wheels |

**Environment (already verified):** Apple M4 Pro, 48 GB unified memory, macOS 15.7.4, ~408 GB
free disk, `uv` 0.7.12, Ollama 0.31.1 installed. Disk/RAM are not constraints.

---

## Key technical facts (confirmed against current Pipecat via context hub)

Confirmed import paths and constructors (Pipecat ≥ 1.0):
```python
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.services.whisper.stt import WhisperSTTServiceMLX, MLXModel
from pipecat.services.ollama.llm import OLLamaLLMService   # base_url default http://localhost:11434/v1
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams
```
- `LocalAudioTransportParams(TransportParams)` accepts `audio_in_enabled`, `audio_out_enabled`,
  `input_device_index`, `output_device_index`, `vad_analyzer=`, `turn_analyzer=`.
- `OLLamaLLMService` extends `OpenAILLMService`; talks to a local Ollama server. No API key.
- **Critical offline caveat:** Kokoro, Whisper-MLX, Silero VAD, and Smart Turn v3 all
  **auto-download model files from HuggingFace on first use**. Offline operation requires a
  one-time online **warm-up run** to populate caches first. This is the #1 thing to get right.

Reference example (STT-only local audio, to mirror the wiring style):
`pipecat-ai/pipecat-examples` → `local-input-select-stt/bot.py` (uses `LocalAudioTransport` +
`select_audio_device.py` Textual device picker). We extend it to full duplex (add LLM + TTS +
audio output + VAD + smart turn).

---

## Repository layout (all under the repo dir; models gitignored)

```
locat/
├── .python-version              # 3.12
├── pyproject.toml               # uv project, pinned deps + extras
├── .env.example                 # documented config knobs (copy to .env)
├── .gitignore                   # excludes models/, .venv, .env
├── README.md                    # setup, model-pull, offline-test instructions
├── bot.py                       # the pipeline (main entry: `uv run bot.py`)
├── config.py                    # env-driven settings (models, device indices, voice)
├── prompts/
│   └── financial_advisor.py     # system prompt (v1 personality)
├── select_audio_device.py       # optional interactive mic/speaker picker (Textual)
├── scripts/
│   ├── prefetch_models.py       # warm-up: force all HF model downloads while online
│   └── run_ollama.sh            # starts `ollama serve` with OLLAMA_MODELS → ./models/ollama
└── models/                      # ALL checkpoints live here (gitignored)
    ├── huggingface/             # HF_HOME → whisper-mlx, silero, smart-turn caches
    ├── kokoro/                  # kokoro onnx + voices
    └── ollama/                  # OLLAMA_MODELS store (qwen2.5:14b)
```

**Keeping checkpoints in the repo dir:** point cache env vars into `./models/` at process start
(in `config.py` / `.env`): `HF_HOME=./models/huggingface`, Kokoro `model_path`/`voices_path`
→ `./models/kokoro`, and run the Ollama server with `OLLAMA_MODELS=./models/ollama`. Ollama is a
separate daemon, so its store is relocated via `scripts/run_ollama.sh` (documented in README).

---

## Implementation phases (ordered; each is independently verifiable)

### Phase 0 — Scaffold & Python env
- `uv init`, create `.python-version` (3.12), `pyproject.toml`.
- `.gitignore`: `models/`, `.venv/`, `.env`, `__pycache__/`, `*.pyc`.
- **Verify:** `uv run python -c "import sys; print(sys.version)"` reports 3.12.x.

### Phase 1 — Dependencies
- Add Pipecat with the local-service extras. Exact extra names must be confirmed against the
  current release at implementation time (via context hub / PyPI), but expected set:
  `pipecat-ai[silero, whisper, kokoro, ollama, local-smart-turn]` (or the MLX/whisper +
  kokoro-onnx equivalents), plus `pyaudio`, `python-dotenv`, `loguru`, and `textual` (device
  picker). PyAudio needs PortAudio: `brew install portaudio` (document in README).
- **Verify:** `uv sync` succeeds; `uv run python -c "from pipecat.transports.local.audio import LocalAudioTransport"` imports cleanly (repeat for each service class listed above). Fix any
  deprecated import paths using the context hub `check_deprecation` tool.

### Phase 2 — Model acquisition (the online step)
- `scripts/run_ollama.sh`: export `OLLAMA_MODELS=$PWD/models/ollama`, then `ollama serve` (bg),
  then `ollama pull qwen2.5:14b` (model tag configurable).
- `scripts/prefetch_models.py`: with `HF_HOME` pointed into `./models/`, instantiate each service
  once (or call their download hooks) to force Whisper-MLX, Kokoro, Silero VAD, and Smart Turn v3
  model files onto disk.
- **Verify:** After running both, `./models/` contains ollama blobs + HF caches + kokoro files;
  `ollama list` shows `qwen2.5:14b` served from the repo store.

### Phase 3 — The bot pipeline (`bot.py`)
Full-duplex pipeline:
```
transport.input() → STT → user-context-aggregator → LLM → TTS → transport.output() → assistant-context-aggregator
```
- Transport: `LocalAudioTransportParams(audio_in_enabled=True, audio_out_enabled=True,
  vad_analyzer=SileroVADAnalyzer(), turn_analyzer=LocalSmartTurnAnalyzerV3(...))`, device indices
  from config.
- STT: `WhisperSTTServiceMLX(settings=...Settings(model=MLXModel.<size>))` — default a large-v3
  variant; configurable.
- LLM: `OLLamaLLMService(model=<env>, base_url=<env default localhost:11434/v1>)`.
- TTS: `KokoroTTSService(settings=...Settings(voice=<env>), model_path=..., voices_path=...)`.
- Context: use the **current** universal LLM context + aggregator pattern
  (`pipecat.processors.aggregators.llm_response_universal` / `LLMContext`) — confirm exact
  class names against the context hub at implementation time (the API around context aggregators
  has changed across releases; do not assume). Seed context with the financial system prompt.
- `PipelineTask(..., params=PipelineParams(allow_interruptions=True))`; `PipelineRunner`.
- Greeting: on client/transport ready, have the bot speak a short opening line.
- **Verify:** see Phase 5.

### Phase 4 — Config & system prompt
- `config.py`: read env (`.env`) for `LLM_MODEL`, `OLLAMA_BASE_URL`, `WHISPER_MODEL`,
  `KOKORO_VOICE`, `INPUT_DEVICE_INDEX`, `OUTPUT_DEVICE_INDEX`, and the cache-dir vars. Sensible
  defaults so `uv run bot.py` works with zero config.
- `prompts/financial_advisor.py`: a system prompt framing the bot as a thoughtful, private
  financial *thinking partner* — asks clarifying questions, reasons about trade-offs, explicitly
  states it is not a licensed advisor, and never claims access to real accounts (v1 has no data
  access). Keep it tuned for spoken output (concise, conversational, no markdown).
- **Verify:** changing `LLM_MODEL` / `KOKORO_VOICE` in `.env` visibly changes behavior.

### Phase 5 — README & example polish (last autonomous step)
- README: what it is, hardware notes, `brew install portaudio`, `uv sync`, model-pull steps
  (`scripts/run_ollama.sh` + `prefetch_models.py`) with approximate download sizes, how to run,
  how to run **offline**, how to pick audio devices, and a config table. Note the repo is the
  latest Pipecat release and models are gitignored + fetched from HuggingFace/Ollama.
- **Explicitly state: no API keys required.** Every service is local/auth-free — Ollama pulls from
  its own registry; Whisper-MLX / Kokoro / Silero / Smart Turn download anonymously from
  HuggingFace (none gated). `.env` holds *config only* (model names, voice, device indices), never
  secrets. The only network use is the one-time anonymous model download in Phase 2.
- Note the deferred roadmap (memory → RAG → tools) so readers see the progression.
- **Verify:** the README documents the full path clone → `uv sync` → model pull → run → offline
  run, with nothing missing (completeness self-check; the offline run itself is Phase 6).

### Phase 6 — Offline verification (HUMAN-GATED — the actual success criterion)
> This is the final step and **must be run by a human** — it needs a person speaking into a mic and
> toggling Wi-Fi. Ralph must NOT fake it: when everything up to here is done, ralph writes the exact
> steps below into `RALPH_BLOCKED.md` and stops.
1. **Online smoke test:** with Ollama running, `uv run bot.py`, speak, confirm you hear a spoken
   reply. Confirm interruptions work (talk over the bot; it yields).
2. **Offline test:** quit anything network-y, **turn off Wi-Fi / enable Airplane Mode**, ensure
   the local Ollama server is running (from `run_ollama.sh`), `uv run bot.py`, and hold a full
   spoken conversation. This passing is the definition of done for v1.
3. Sanity-check no service silently reaches the network (watch logs for download/HTTP attempts on
   the offline run — there should be none after Phase 2 warm-up).

---

## Deferred (explicitly NOT in v1)
- Persistent memory across sessions (local JSON/SQLite).
- Document RAG over personal financial files (local embeddings + vector store).
- Function-calling tools (compound interest, amortization, savings-goal calculators).
Repo structure leaves room for each; they become their own plan/spec cycles.

---

## Verification summary (end-to-end)
- `uv sync` clean; all service classes import.
- `scripts/run_ollama.sh` + `scripts/prefetch_models.py` populate `./models/` fully.
- **Online:** `uv run bot.py` → spoken back-and-forth, interruptions work.
- **Offline (Airplane Mode):** `uv run bot.py` → full spoken conversation, zero network calls.
- README reproducible from a clean clone.

## Open items to confirm at implementation time (do not guess — use the context hub)
1. Exact Pipecat **extra names** for whisper-mlx / kokoro / ollama / local-smart-turn in the
   current release.
2. Exact **context aggregator** API (`LLMContext` / universal aggregator class names).
3. `MLXModel` enum member names and the best default Whisper size for latency vs. accuracy.
4. `LocalSmartTurnAnalyzerV3` constructor args (model path / smart-turn model source).
5. Kokoro default `voice` id and whether `model_path`/`voices_path` are needed to pin its cache
   into `./models/kokoro`.
