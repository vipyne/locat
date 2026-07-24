# locat — a local, fully-offline Pipecat voice bot

> WIP but `bot.py`, `bot_web.py`, and `bot_moq.py` all work. Documentation could use some love.


A voice bot that runs **100% offline**. Speech-to-text, the language model, and
text-to-speech are all local services, and the "transport†" is your machine's own
audio hardware — the microphone and speakers. The success test is simple: turn off
Wi-Fi, start the bot, and hold a spoken conversation.

The v1 personality is a **private financial thinking partner**: something you can
talk through money decisions with, out loud, knowing nothing you say leaves the
computer. Because those details are personal, no cloud LLM is acceptable — the model
is local by design.

Built on the latest [Pipecat](https://github.com/pipecat-ai/pipecat) release
(≥ 1.6). This repo is meant to double as a clear, reproducible **example** of how to
wire up a fully-local Pipecat bot.

†Audio is hard and there are a few ways to handle it in this scenario. See "how do you
solve a problem like echo cancellation?"

> **No API keys. Ever.** Every service is local and auth-free. `.env` holds *config
> only* — model names, a voice, device indices, cache paths — never secrets. The
> only time the bot touches the network is the one-time, anonymous model download in
> [Step 2](#2-fetch-the-models-the-one-time-online-step). See
> [No secrets, no keys](#no-secrets-no-keys).

---

## What's inside

| Component | Service | Notes |
|---|---|---|
| Speech-to-text | `WhisperSTTServiceMLX` | Apple-Silicon-optimized Whisper via MLX |
| Language model | Qwen2.5-14B-Instruct via **Ollama** | Local, OpenAI-compatible endpoint; env-configurable |
| Text-to-speech | `KokoroTTSService` | Natural local neural voice (kokoro-onnx) |
| Turn-taking | Silero VAD + Local Smart Turn v3 | Barge-in / interruptions, fully local (bundled with Pipecat) |
| Transport | `LocalAudioTransport` | PyAudio mic + speaker I/O (requires headphones) |
| Alternative transports | `SmallWebRTC` / `MoQ` | run in a browser → free echo cancellation via `getUserMedia` |


---

## Requirements

- **Apple Silicon Mac.** Whisper-MLX uses Apple's MLX framework. Developed on an M4
  Pro / 48 GB / macOS 15.7; ~15 GB free disk for the models.
- **[uv](https://docs.astral.sh/uv/)** — the Python package manager used throughout.
  Python **3.12** is pinned (`.python-version`); 3.14 is too new for the ML wheels.
- **[Ollama](https://ollama.com/)** — serves the local LLM.
- **PortAudio** — PyAudio's native dependency.

---

## Setup

### 1. Clone, install system deps, and sync the environment

```bash
git clone <this-repo-url> locat && cd locat
brew install portaudio        # PyAudio needs this
uv sync                       # creates .venv and installs everything (Python 3.12)
```

Optionally copy the config template (everything is optional — the bot runs with an
empty or absent `.env`):

```bash
cp .env.example .env
```

### 2. Fetch the models (the one-time online step)

Four model-backed components need weights. Two download from Hugging Face
(anonymously — none are gated); the LLM is pulled by Ollama. Silero VAD and Smart
Turn v3 ship *inside* the Pipecat package, so they download nothing.

All checkpoints are steered into the repo-local **`./models/`** tree (gitignored),
so everything the bot needs lives next to the code.

**a) Pull the LLM into the repo's Ollama store:**

```bash
bash scripts/run_ollama.sh
```

This relocates Ollama's model store to `./models/ollama`, starts `ollama serve`,
pulls the model (`qwen2.5:14b` by default, ~9 GB), and keeps the server running in
the foreground for the bot. Override the model with
`LLM_MODEL=qwen2.5:7b bash scripts/run_ollama.sh`. Leave this running (or re-run it)
whenever you use the bot — it's the local LLM server.

**b) Prefetch the Whisper + Kokoro weights:**

```bash
uv run python scripts/prefetch_models.py
```

Downloads Whisper-MLX (`large-v3-turbo`, ~1.5 GB) into `./models/huggingface` and
Kokoro's ONNX model + voices (~350 MB) into `./models/kokoro`, and load-checks the
bundled Silero VAD + Smart Turn v3 (no download). Run this **once, while online**;
after it finishes the bot can run with Wi-Fi off.

Approximate total download: **~11 GB** (9 GB LLM + 1.5 GB Whisper + 0.35 GB Kokoro).

### 3. Run

With the Ollama server from step 2a running:

```bash
uv run bot.py
```

The bot speaks a short greeting, then listens. Talk to it; it replies through your
speakers. Talk over it and it yields (barge-in). Press **Ctrl-C** to stop.

**One-command launch:** `./start.sh` brings up the repo-local Ollama server (if it
isn't already running) and then starts the bot — so you can skip the manual
`run_ollama.sh` in step 2a. For the browser bots (speakers, *with* echo
cancellation), use `./start_web.sh` or `./start_moq.sh` — see
[echo cancellation](#how-do-you-solve-a-problem-like-echo-cancellation).

### 4. Run offline

Once the models are fetched:

1. Make sure the local Ollama server is running (`bash scripts/run_ollama.sh`).
2. **Turn off Wi-Fi / enable Airplane Mode.** (It won't use the internet if you don't turn off the internet. This is just showing off.)
3. `uv run bot.py` and hold a conversation.

With `LOG_LEVEL=DEBUG` (the default) you can watch the logs and confirm no service
reaches out to the network after the warm-up.

---

## How do you solve a problem like echo cancellation

### Use headphones aka Local local [sic] audio

Because reasons, it's much closer to impossible than just impractical to get native 
macOS AEC (Acoustic Echo Cancellation) to work with pyaudio. So use headphones and 
the bot won't keep interrupting itself.

### Use the web browser's `getUserMedia`

Another fantastic workaround is to use a browser. Not the internet, just the web 
browser. Do this and :tada:, you have echo cancellation.

Two browser bots ship here — same offline brain, different transport. Each start
script brings up Ollama and serves a local page (still fully offline — the browser
talks to the bot over loopback, no internet):

```bash
./start_web.sh    # WebRTC   → open http://localhost:7860/client
./start_moq.sh    # MoQ/QUIC → open http://localhost:7860, pick "Media over QUIC" (lower latency)
```

---

## No secrets, no keys

There are **no API keys** anywhere in this project, and there's nowhere to put one:

- **Ollama** pulls the LLM from its own public registry and serves it locally.
- **Whisper-MLX, Kokoro, Silero VAD, Smart Turn v3** download anonymously from
  Hugging Face (none are gated) — or, for Silero/Smart Turn, ship bundled with
  Pipecat.

`.env` is **config only** — model names, a voice, device indices, cache paths. It is
gitignored, but nothing secret ever belongs in it. The single network event in the
bot's entire lifecycle is the one-time, anonymous model download in step 2.

---

## Configuration

Every knob is an environment variable (read from `.env` if present). All are
optional — the shown value is the default. See [`.env.example`](.env.example) for
the copy-paste template.

| Variable | Default | What it does |
|---|---|---|
| `LLM_MODEL` | `qwen2.5:14b` | Ollama model tag. Same string `run_ollama.sh` pulls and the bot serves. Smaller/faster: `qwen2.5:7b`. |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible Ollama endpoint (note the trailing `/v1`). |
| `WHISPER_MODEL` | `LARGE_V3_TURBO` | `MLXModel` member: `TINY`, `MEDIUM`, `LARGE_V3`, `LARGE_V3_TURBO`. Must match what you prefetched. |
| `KOKORO_VOICE` | `af_heart` | Kokoro voice id (e.g. `af_bella`, `am_michael`, `bf_emma`). |
| `INPUT_DEVICE_INDEX` | *(system default)* | PyAudio mic index. |
| `OUTPUT_DEVICE_INDEX` | *(system default)* | PyAudio speaker index. |
| `GREETING` | *"Hi. I'm your private, offline financial thinking partner…"* | Opening line spoken on startup. |
| `GREETING_DELAY_SECS` | `1.0` | Delay before the greeting (lets the audio-out stream spin up). |
| `LOG_LEVEL` | `DEBUG` | Loguru level for stderr. `DEBUG` surfaces each service's activity — handy for the offline check. |
| `HF_HOME` | `./models/huggingface` | Hugging Face cache root (Whisper-MLX weights). *Advanced.* |
| `KOKORO_MODEL_PATH` | `./models/kokoro/kokoro-v1.0.onnx` | Kokoro ONNX model path. *Advanced.* |
| `KOKORO_VOICES_PATH` | `./models/kokoro/voices-v1.0.bin` | Kokoro voices bundle path. *Advanced.* |
| `OLLAMA_MODELS` | `./models/ollama` | Ollama store location (used by `run_ollama.sh`). *Advanced.* |
| `OLLAMA_HOST` | `127.0.0.1:11434` | Host the Ollama server binds to (used by `run_ollama.sh`). *Advanced.* |

Changing `LLM_MODEL` swaps which local model answers; changing `KOKORO_VOICE`
changes the voice you hear.

---

## Repository layout

All three bots share one offline brain (the same STT → VAD → LLM → TTS pipeline and
builders); they differ only in the transport.

```
locat/
├── bot.py                    # CLI / headphones — LocalAudioTransport
├── bot_web.py                # browser / speakers — SmallWebRTC (free echo cancellation)
├── bot_moq.py                # browser / speakers — MoQ over QUIC (lower latency)
├── config.py                 # env-driven settings, zero-config defaults
├── spoken_text_filter.py     # TTS filter: "$3,000" → "three thousand dollars"
├── prompts/
│   └── financial_advisor.py  # the v1 system prompt
│
├── start.sh                  # one command: bring up Ollama + run bot.py (CLI)
├── start_web.sh              # one command: bring up Ollama + run bot_web.py (WebRTC)
├── start_moq.sh              # one command: bring up Ollama + run bot_moq.py (MoQ)
├── stop.sh                   # stop the background Ollama server
│
├── scripts/
│   ├── run_ollama.sh         # relocate Ollama store + serve + pull the LLM
│   ├── prefetch_models.py    # one-time online warm-up (Whisper + Kokoro)
│   ├── check_audio.py        # diagnostic: raw mic input level meter
│   └── check_vad.py          # diagnostic: Silero VAD confidence/volume vs thresholds
│
├── ralph.sh                  # the "ralph loop" that built this repo (autonomous runner)
├── PROMPT.md                 # per-iteration instructions for the loop
├── RALPH.md                  # operator runbook for the loop
├── PLAN.md                   # the approved build plan the loop followed
│
├── .env.example              # documented config knobs (copy to .env)
├── .python-version           # 3.12
├── pyproject.toml            # uv project + pinned deps
├── uv.lock                   # locked dependency versions
│
└── models/                   # ALL checkpoints live here (gitignored; created by setup)
    ├── huggingface/          # Whisper-MLX
    ├── kokoro/               # Kokoro onnx + voices
    └── ollama/               # Ollama LLM store
```

---

## Roadmap

v1 is conversation only; the repo is structured so later capabilities layer in
cleanly, each its own build cycle:

1. **Persistent memory** across sessions (local JSON/SQLite).
2. **Document RAG** over your own financial files (local embeddings + vector store).
3. **Function-calling tools** (compound interest, amortization, savings-goal
   calculators).

---

## Not financial advice

The bot is a private *thinking partner*, not a licensed financial advisor. It has no
access to your real accounts and won't invent your numbers. For big, irreversible,
or high-stakes decisions, confirm with a qualified professional.
Ha, claude wrote that^ when I said I wanted to create a fully offline bot that I could 
talk to about my personal finances.

## Emojis
claude did _not_ add enough/any emojis so: 
🎉🎊🥳🎈🎁🎀🌟✨💫⭐🌈🔥💥⚡☀️🌙🌛🌜🌞🪐🌍🌎🌏🌊🏔️⛰️🌋🗻🏕️🏖️🏜️🏝️🌅🌄🌇🌆🏙️🌃🌌🎆🎇🌠🌉🍀🌿🍃🌾🌵🌴🌳🌲🎄🌰🍄🌻🌺🌸🌼🌷🌹🥀💐🏵️🌊🐠🐟🐬🐳🐋🦈🐙🦑🦐🦞🦀🐚🐌🦋🐛🐝🐞🦗🕷️🦂🐢🐍🦎🦖🦕🐙🦭🦦🦥🐾🐕🐈🐇🐿️ 🦫🦃🐔🐓🐣🐤🐥🦆🦢🦅🦉🦚🦜🕊️🐧🐦🦩🦨🐘🦏🦛🐪🐫🦒🦓🐂🐃🐄🐎🐖🐏🐑🦙🐐🦌🐕‍🦺🐈‍⬛🦮🐩🐾🍎🍏🍐🍊🍋🍌🫐🍈🍒🍑🥭🍍🥥🥝🍅🍆🥑🥦🥬🥒🌶️ 🫑🌽🥕🫒🧄🧅🥔🍠🥐🥯🍞🥖🥨🧀🥚🍳🧈🥞🧇🥓🥩🍗🍖🌭🍔🍟🍕🫓🥪🥙🧆🌮🌯🫔🥗🥘🫕🥫🍝🍜🍲🍛🍣🍱🥟🦪🍤🍙🍚🍘🍥🥠🥮🍢🍡🍧🍨🍦🥧🧁🍰🎂🍮🍭🍬🍫🍿🍩🍪🌰🥜🍯🥛🍼☕🫖🍵🧃🥤🧋🍶🍺🍻🥂🍷🥃🍸🍹🧉🍾🧊🥄🍴🍽️🥣🥡🥢🧂⚽🏀🏈⚾🥎🎾🏐🏉🥏🎱🪀🏓🏸🏒🏑🥍🏏🪃🥅⛳🪁🏹🎣🤿🥊🥋🎽🛹🛼🛷⛸️ 🥌🎿⛷️ 🏂🪂🏋️ 🤼🤸⛹️ 🤺🤾🏌️ 🏇🧘🏄🏊🤽🚣🧗🚵🚴🏆🥇🥈🥉🏅🎖️ 🏵️ 🎗️ 🎫🎟️ 🎪🤹🎭🩰🎨🎬🎤🎧🎼🎹🥁🎷🎺🎸🪕🎻🎲♟️🎯🎳🎮🎰🧩
