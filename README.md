# locat — a local, fully-offline Pipecat voice bot

> WIP but isn't everything?

A voice bot that runs Key-Free & **100% offline**. Speech-to-text, the language model, 
and text-to-speech are all local services, and the "transport†" is your machine's own
audio hardware — the microphone and speakers.

Built on the latest [Pipecat](https://github.com/pipecat-ai/pipecat) release
(≥ 1.7). This repo is meant to double as a clear, reproducible **example** of how to
wire up a fully-local Pipecat bot.

The v1 personality is a **private financial thinking partner**: something you can
talk through money decisions with, out loud, knowing nothing you say leaves the
computer.

†Audio is hard and there are a few ways to handle it in this scenario. See "how do you
solve a problem like echo cancellation?"

---

## Requirements

- **Apple Silicon Mac recommended** — Whisper-MLX (the default STT) uses Apple's
  MLX framework and only runs there. Intel Macs and Linux work too: they default
  to CPU STT (`faster_whisper`) automatically. Windows: not yet (WSL works).
- ~15 GB free disk for the models
- Python **3.12** is pinned as 3.14 is too new for the ML wheels.
- **A plain `uv sync` needs no compiler** — everything in the base install comes
  from a prebuilt wheel. Several deps (onnxruntime, numba/llvmlite, cryptography)
  have already dropped Intel-mac wheels, so `pyproject.toml` pins those back to
  their last Intel-mac release under `[tool.uv]`. If `uv sync` ever starts
  building something from source, run `python3 scripts/check_wheels.py` to see
  which platform lost a wheel.
  The one exception is PyAudio, which has no macOS/Linux wheels — so it is an
  opt-in extra (`--extra local-audio`) needed only by the headphones front-end.
- **[uv](https://docs.astral.sh/uv/)** — Python package manager.
- **[Ollama](https://ollama.com/)** — serves the local LLM.
- **PortAudio** OR **any web browser** — PyAudio's native dependency / audio
  handling. Browser front-ends need neither PortAudio nor a compiler.

---

## Quickstart Setup

### 0. The [short short version](https://www.youtube.com/watch?v=5X4HYA-lB-U):

> [!NOTE]
> The first pull will take a few minutes to download the models.

#### Browser-based (for echo cancellation):
```bash
git clone git@github.com:vipyne/locat.git && cd locat
uv sync
bash scripts/run_ollama.sh
uv run python scripts/prefetch_models.py
```
Ctrl+C; then turn off wi-fi if you want to show off and then:

```bash
./start.sh
```
Open http://localhost:7860, choose "Media over QUIC", click Connect & have a conversation. (Prefer WebRTC? ./start.sh -t webrtc → http://localhost:7860/client.)

Or...

#### PyAudio & headphones (for echo cancellation):

> [!IMPORTANT]
> Use headphones 🎧

```bash
git clone git@github.com:vipyne/locat.git && cd locat
brew install portaudio            # Debian: sudo apt install portaudio19-dev
uv sync --extra local-audio       # the extra adds PyAudio (needs PortAudio)
bash scripts/run_ollama.sh
uv run python scripts/prefetch_models.py
```
Ctrl+C; then turn off wi-fi if you want to show off and then:

```bash
./start.sh -t headphones
```
Have a conversation.

## Setup

### 1. Clone, install system deps, and sync the environment

```bash
git clone git@github.com:vipyne/locat.git && cd locat
uv sync                       # creates .venv and installs everything (Python 3.12)
```

That is all you need for the browser front-ends. For the headphones front-end
(`bot.py`), PyAudio has to compile against PortAudio, so install it and opt into
the extra:

```bash
brew install portaudio                # Debian: sudo apt install portaudio19-dev
uv sync --extra local-audio
```

> [!NOTE]
> `uv sync` uninstalls any extra you don't pass, so keep listing the ones you
> want: `uv sync --extra local-audio --extra piper`. (`./configure.sh -i` preserves
> whatever is already installed.)

Optionally copy the config template (everything is optional — the bot runs with an
empty or absent `.env`):

```bash
cp env.example .env
```

### 2. Fetch the models (the one-time online step)

Four model-backed components need weights. Two download from Hugging Face
(anonymously — none are gated); the LLM is pulled by Ollama. Silero VAD and Smart
Turn v3 ship *inside* the Pipecat package, so they download nothing.

All checkpoints are steered into **one directory** — `LOCAT_MODEL_DIR`, default
`./models/` (gitignored) — so everything the bot needs lives next to the code.
Every engine follows it, so you can move the whole lot anywhere:

```bash
mv ./models /Volumes/T7/locat-models
echo 'LOCAT_MODEL_DIR=/Volumes/T7/locat-models' >> .env
```

Absolute paths and `~` both work; relative paths resolve against the repo root,
not your shell's cwd. `config.py` and `scripts/model_dir.sh` implement the same
rules, so Python and the shell scripts always agree.

**a) Pull the LLM into the repo's Ollama store:**

```bash
bash scripts/run_ollama.sh
```

This relocates Ollama's model store to `./models/ollama`, starts `ollama serve`,
pulls the model (`qwen2.5:14b` by default, ~9 GB), and keeps the server running in
the foreground for the bot. Override the model with
`LOCAT_LLM_MODEL=qwen2.5:7b bash scripts/run_ollama.sh`. Leave this running (or re-run it)
whenever you use the bot — it's the local LLM server.

**b) Prefetch the Whisper + Kokoro weights:**

```bash
uv run python scripts/prefetch_models.py
```

Downloads Whisper-MLX (`large-v3-turbo`, ~1.5 GB) into `$LOCAT_MODEL_DIR/huggingface`
and Kokoro's ONNX model + voices (~350 MB) into `$LOCAT_MODEL_DIR/kokoro`, and load-checks the
bundled Silero VAD + Smart Turn v3 (no download). Run this **once, while online**;
after it finishes the bot can run with Wi-Fi off.

Approximate total download: **~11 GB** (9 GB LLM + 1.5 GB Whisper + 0.35 GB Kokoro).

### 3. Run

> [!IMPORTANT]
> Use headphones 🎧

With the Ollama server from step 2a running:

```bash
uv run bot.py
```

The bot speaks a short greeting, then listens. Talk to it; it replies through your
speakers. Talk over it and it yields (barge-in). Press **Ctrl-C** to stop.

**One-command launch:** `./start.sh` brings up the repo-local Ollama server (if it
isn't already running), prints the exact STT/LLM/TTS models in play, and serves the
MoQ browser bot — so you can skip the manual `run_ollama.sh` in step 2a. Pick a
different transport with `-t`: `./start.sh -t webrtc` (browser, SmallWebRTC) or
`./start.sh -t headphones` (local audio hardware) — see
[echo cancellation](#how-do-you-solve-a-problem-like-echo-cancellation). Not sure
what your machine can handle? `./configure.sh` prints recommended STT/LLM/TTS cascades
sized to your hardware (add `-v` for the full hardware profile and per-slot model
catalogs ranked by fit, or `-i` to interactively pick a combo the script
sanity-checks against your hardware).

### 4. Run offline

Once the models are fetched:

1. Make sure the local Ollama server is running (`bash scripts/run_ollama.sh`).
2. **Turn off Wi-Fi / enable Airplane Mode.** (It won't use the internet if you don't turn off the internet. This is just showing off.)
3. `uv run bot.py` and hold a conversation.

With `LOCAT_LOG_LEVEL=DEBUG` (the default) you can watch the logs and confirm no service
reaches out to the network after the warm-up.

---

## How do you solve a problem like echo cancellation

### Use headphones

Because reasons, it's much closer to impossible than just impractical to get native 
macOS AEC (Acoustic Echo Cancellation) to work with pyaudio. Use headphones and 
the bot won't keep interrupting itself.

### Use the web browser's `getUserMedia`

Another fantastic workaround is to use a browser. Not the internet, just the web 
browser. Do this and 🎉, you have echo cancellation.

Two browser transports ship here — same offline brain, different transport. `start.sh`
brings up Ollama and serves a local page (still fully offline — the browser talks to
the bot over loopback, no internet):

```bash
./start.sh              # MoQ/QUIC → open http://localhost:7860, pick "Media over QUIC"
./start.sh -t webrtc    # WebRTC   → open http://localhost:7860/client
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

## What's inside

| Component | Service | Notes |
|---|---|---|
| Speech-to-text | `WhisperSTTServiceMLX` *(default)* | Apple-Silicon-optimized Whisper via MLX. Alternatives via `LOCAT_STT_ENGINE`: `faster_whisper` (CPU), `moonshine` (tiny CPU ONNX) |
| Language model | Qwen2.5-14B-Instruct via **Ollama** | Local, OpenAI-compatible endpoint; env-configurable |
| Text-to-speech | `KokoroTTSService` *(default)* | Natural local neural voice (kokoro-onnx). Alternative via `LOCAT_TTS_ENGINE`: `piper` |
| Turn-taking | Silero VAD + Local Smart Turn v3 | Barge-in / interruptions, fully local (bundled with Pipecat) |
| Transport | `LocalAudioTransport` | PyAudio mic + speaker I/O (requires headphones) |
| Alternative transports | `SmallWebRTC` / `MoQ` | run in a browser → free echo cancellation via `getUserMedia` |

---

## RAG (documents)

The bot can ground its answers in your own documents — fully offline, no vector
database. Drop `.txt`, `.md`, or `.pdf` files into `./data/`, then:

```bash
ollama pull nomic-embed-text   # one-time, while online
./locat.sh index-rag
```

On every user turn the bot embeds the query, picks the `LOCAT_RAG_TOP_K` best
chunks by cosine similarity, and injects them into the LLM context as a single
system message ("Relevant excerpts from the user's documents …") that names
each source file and page so the model can cite them. `./locat.sh status`
shows the index; after adding or editing documents, re-run
`./locat.sh index-rag` (it rebuilds from scratch). With no index the bot just
runs without document grounding.

Bot code reaches all of this through exactly two functions, `rag.index(...)`
and `rag.retrieve(...)` — everything behind them (pypdf extraction, chunking,
Ollama embeddings, numpy cosine) is an implementation detail of `rag.py`.

### Is it really reading my documents? The canary trick

Plant a fact the model cannot possibly know, and ask for it:

```bash
echo "The secret passphrase for the tax vault is BLUE PELICAN 47." > data/canary.txt
./locat.sh index-rag
./locat.sh start -t headphones
```

Ask *"what's the passphrase for the tax vault?"* — a correct answer can only
have come from retrieval, and `tail -f .locat/bot.log | grep "rag:"` shows the
exact file, page, and score injected on every turn. The same canary also
demonstrates the index lifecycle: delete `data/canary.txt` and ask again
without re-indexing — the bot still answers, because it reads the built index,
never `data/` itself. Re-run `./locat.sh index-rag` and ask a third time — now
the passphrase is gone. Moral: the index only changes when you rebuild it.

For a negative control, `mv models/rag-index /tmp/` and restart: the bot logs
`no RAG index found` and runs ungrounded. To inspect the index directly,
`uv run python rag.py stats`, or grep `models/rag-index/chunks.jsonl` — it's
plain JSON.

---

## One place for all models: consolidate

HuggingFace defaults to `~/.cache/huggingface`, Ollama to `~/.ollama/models` —
models you pulled before locat (or outside it) live there, and locat uses them
from there automatically: the bot loads weights straight from whichever cache
has them, and `./locat.sh status` / `./locat.sh models` print the real path.
`./locat.sh consolidate` is an optional convenience on top — it adopts external
models into `LOCAT_MODEL_DIR` with symlinks so one `ls -al ./models` browses
every model with its real path (and shows what a zip-and-go copy of the repo
would actually include):

```bash
./locat.sh consolidate -n   # dry run: print what would be adopted
./locat.sh consolidate      # create the links
```

- **HuggingFace, per model:** each model in your home cache that the locat
  store lacks becomes a symlink inside `models/huggingface/hub/`, loadable
  through `LOCAT_MODEL_DIR` like any owned model. A broken partial download in
  the locat store is replaced by a link to a complete external copy.
- **Ollama, whole store:** models share content-addressed blobs, so adoption
  links `models/ollama -> ~/.ollama/models` when the locat store is empty. If
  both stores contain models, consolidate reports the conflict and touches
  nothing (skipped while an ollama server is running).

Nothing outside the repo is ever moved, modified, or deleted; the only writes
are symlinks (plus removal of locat-owned 0-byte `.incomplete` stubs).
`./locat.sh status` and `./locat.sh models` label adopted models
`(borrowed → /real/path)`. Borrowed means borrowed: a re-download of that
model follows the symlink into your home cache, and zipping up the repo
excludes borrowed weights — delete the link and `ollama pull` /
`uv run python scripts/prefetch_models.py` to own a copy instead.

---

## Configuration

Every knob is an environment variable (read from `.env` if present). All are
optional — the shown value is the default. See [`env.example`](env.example) for
the copy-paste template.

**`LOCAT_` means it's ours.** Anything read by this repo carries the prefix, so
you can tell at a glance what's safe to change and what belongs to someone else.
Exactly four variables are **external** — read by third-party software, keeping
their upstream names because renaming them would break the tool that reads them:

| External variable | Read by |
|---|---|
| `HF_HOME`, `HF_HUB_DISABLE_PROGRESS_BARS` | `huggingface_hub` |
| `OLLAMA_MODELS`, `OLLAMA_HOST` | the `ollama` binary |

If you already export one of those globally, it affects locat too. Note that
`LOCAT_OLLAMA_BASE_URL` is *ours* despite the name — it's the URL the bot dials,
not something ollama reads.

| Variable | Default | What it does |
|---|---|---|
| `LOCAT_LLM_MODEL` | `qwen2.5:14b` | Ollama model tag. Same string `run_ollama.sh` pulls and the bot serves. Smaller/faster: `qwen2.5:7b`. |
| `LOCAT_OLLAMA_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible Ollama endpoint (note the trailing `/v1`). The RAG embedder talks to the same host minus `/v1`. |
| `LOCAT_EMBED_MODEL` | `nomic-embed-text` | Ollama model tag for RAG embeddings. Pull it with `ollama pull nomic-embed-text`. |
| `LOCAT_RAG_DATA_DIR` | `./data` | The documents to index (`.txt`/`.md`/`.pdf`) — put files here, then `./locat.sh index-rag`. Absolute, `~`, or relative-to-repo. |
| `LOCAT_RAG_INDEX_DIR` | `$LOCAT_MODEL_DIR/rag-index` | Where the built index lives (`chunks.jsonl`, `embeddings.npy`, `manifest.json`). |
| `LOCAT_RAG_TOP_K` | `4` | Retrieved chunks injected into the LLM context per user turn. |
| `LOCAT_RAG_CHUNK_TOKENS` | `500` | Chunk budget in whitespace-split words. |
| `LOCAT_RAG_CHUNK_OVERLAP` | `50` | Words repeated between consecutive chunks. |
| `LOCAT_STT_ENGINE` | `whisper_mlx`* | STT engine `services.py` builds: `whisper_mlx`, `faster_whisper`, or `moonshine` (`uv sync --extra moonshine`). *Default is `faster_whisper` on non-Apple-Silicon machines. |
| `LOCAT_WHISPER_MODEL` | `LARGE_V3_TURBO` | `MLXModel` member: `TINY`, `MEDIUM`, `LARGE_V3`, `LARGE_V3_TURBO`. Must match what you prefetched. |
| `LOCAT_FASTER_WHISPER_MODEL` | `DISTIL_MEDIUM_EN` | faster-whisper model (when `LOCAT_STT_ENGINE=faster_whisper`); downloads on first use. |
| `LOCAT_MOONSHINE_MODEL` | `SMALL_STREAMING` | Moonshine model (when `LOCAT_STT_ENGINE=moonshine`); downloads on first use. |
| `LOCAT_TTS_ENGINE` | `kokoro` | TTS engine `services.py` builds: `kokoro` or `piper` (`uv sync --extra piper`; piper-tts is GPL-3.0). |
| `LOCAT_KOKORO_VOICE` | `af_heart` | Kokoro voice id (e.g. `af_bella`, `am_michael`, `bf_emma`). |
| `LOCAT_PIPER_VOICE` | `en_US-lessac-medium` | Piper voice id (when `LOCAT_TTS_ENGINE=piper`); downloads (~60 MB) on first use into `./models/piper`. |
| `LOCAT_INPUT_DEVICE_INDEX` | *(system default)* | PyAudio mic index. |
| `LOCAT_OUTPUT_DEVICE_INDEX` | *(system default)* | PyAudio speaker index. |
| `LOCAT_GREETING` | *"Hi. I'm your private, offline financial thinking partner…"* | Opening line spoken on startup. |
| `LOCAT_GREETING_DELAY_SECS` | `1.0` | Delay before the greeting (lets the audio-out stream spin up). |
| `LOCAT_MOQ_SUBSCRIBER_TIMEOUT` | `15.0` | Max seconds the MoQ bot holds its greeting for the browser's audio subscription (MoQ has no replay — speaking earlier drops the audio). On timeout it greets anyway. |
| `LOCAT_MOQ_AUDIO_AHEAD_MS` | `500` | Max milliseconds of bot audio written ahead of real time over MoQ. TTS runs faster than real time; uncapped bursts overflow the browser's playback ring buffer and clip the start of every bot turn. Raise only if playback stutters mid-utterance. |
| `LOCAT_LOG_LEVEL` | `DEBUG` | Loguru level for stderr. `DEBUG` surfaces each service's activity — handy for the offline check. |
| `LOCAT_WEB_PORT` | `7860` | Port `bot_moq.py` serves on (used by `./locat.sh start`). |
| `LOCAT_STATE_DIR` | `.locat` | Where `./locat.sh` records the PIDs of the processes it started — the only PIDs `./locat.sh stop` will ever touch. Relative paths resolve against the repo root. |
| `LOCAT_VAD_CONFIDENCE` | `0.7` | Silero speech-probability threshold (0–1) before audio counts as speech. |
| `LOCAT_VAD_MIN_VOLUME` | `0.0` | Absolute-loudness gate. `0.0` disables it, which keeps turn detection level-independent across mics — raise toward `0.3`–`0.6` only if a noisy room false-triggers. |
| `LOCAT_VAD_START_SECS` | `0.2` | Sustained speech before "user started speaking". |
| `LOCAT_VAD_STOP_SECS` | `0.2` | Sustained silence before "user stopped speaking". |
| `LOCAT_MODEL_DIR` | `./models` | **The one directory every model downloads into** — HF cache, Kokoro, Piper and Ollama all hang off it. Absolute, `~`, or relative-to-repo. Move it to relocate everything at once. |
| `HF_HOME` **[external]** | `$LOCAT_MODEL_DIR/huggingface` | Hugging Face cache root (Whisper-MLX, faster-whisper, Moonshine). Set only to split HF out of the shared dir. *Advanced.* |
| `HF_HUB_DISABLE_PROGRESS_BARS` **[external]** | `1` | Silences HuggingFace download progress bars, which otherwise clutter the bot's logs. *Advanced.* |
| `LOCAT_KOKORO_MODEL_PATH` | `$LOCAT_MODEL_DIR/kokoro/kokoro-v1.0.onnx` | Kokoro ONNX model path. *Advanced.* |
| `LOCAT_KOKORO_VOICES_PATH` | `$LOCAT_MODEL_DIR/kokoro/voices-v1.0.bin` | Kokoro voices bundle path. *Advanced.* |
| `LOCAT_PIPER_DOWNLOAD_DIR` | `$LOCAT_MODEL_DIR/piper` | Where Piper voices download. *Advanced.* |
| `OLLAMA_MODELS` **[external]** | `$LOCAT_MODEL_DIR/ollama` | Ollama store location (used by `run_ollama.sh`). Use an absolute path if you set it — ollama resolves relative paths against its own cwd. *Advanced.* |
| `OLLAMA_HOST` **[external]** | `127.0.0.1:11434` | Host the Ollama server binds to (used by `run_ollama.sh`). *Advanced.* |

Changing `LOCAT_LLM_MODEL` swaps which local model answers; changing `LOCAT_KOKORO_VOICE`
changes the voice you hear.

---

## Repository layout

All three bots share one offline brain (the same STT → VAD → LLM → TTS pipeline);
they differ only in the transport. The STT/LLM/TTS services themselves are built in
`services.py`, dispatched on `LOCAT_STT_ENGINE` / `LOCAT_TTS_ENGINE` — so swapping engines (via
`.env` or `./configure.sh -i`) never touches a bot file you may have customized.

```
locat/
├── bot.py                    # CLI / headphones — LocalAudioTransport
├── bot_web.py                # browser / speakers — SmallWebRTC (free echo cancellation)
├── bot_moq.py                # browser / speakers — MoQ over QUIC (lower latency)
├── services.py               # STT/LLM/TTS builders, engine-dispatched (LOCAT_STT_ENGINE / LOCAT_TTS_ENGINE)
├── config.py                 # env-driven settings, zero-config defaults
├── spoken_text_filter.py     # TTS filter: "$3,000" → "three thousand dollars"
├── prompts/
│   └── financial_advisor.py  # the v1 system prompt
│
├── start.sh                  # one command: bring up Ollama + run the bot (-t moq|webrtc|headphones)
├── configure.sh              # what can this machine handle? (-v full report, -i model picker)
├── stop.sh                   # stop the background Ollama server
│
├── scripts/
│   ├── model_dir.sh          # resolves LOCAT_MODEL_DIR for the shell scripts
│   │                         #   (config.py does the same for Python)
│   ├── run_ollama.sh         # relocate Ollama store + serve + pull the LLM
│   ├── prefetch_models.py    # one-time online warm-up (Whisper + Kokoro)
│   ├── check_wheels.py       # tripwire: every dep must have a wheel per platform
│   ├── check_audio.py        # diagnostic: raw mic input level meter
│   └── check_vad.py          # diagnostic: Silero VAD confidence/volume vs thresholds
│
├── ralph/                    # the "ralph loop" that built this repo
│   ├── ralph.sh              #   autonomous agent runner
│   ├── PROMPT.md             #   per-iteration instructions for the loop
│   ├── RALPH.md              #   operator runbook for the loop
│   └── PLAN.md               #   the approved build plan the loop followed
│
├── MODELS_TO_ADD.md          # engines considered but not (yet) wired — and why
├── env.example              # documented config knobs (copy to .env)
├── .python-version           # 3.12
├── pyproject.toml            # uv project + pinned deps
├── uv.lock                   # locked dependency versions
│
└── models/                   # ALL checkpoints live here — $LOCAT_MODEL_DIR,
    │                         # relocatable (gitignored; created by setup)
    ├── huggingface/          # Whisper-MLX + faster-whisper + Moonshine (HF cache)
    ├── kokoro/               # Kokoro onnx + voices
    ├── piper/                # Piper voices (if LOCAT_TTS_ENGINE=piper)
    └── ollama/               # Ollama LLM store
```

---

## Roadmap

v1 is conversation only; the repo is structured so later capabilities layer in
cleanly, each its own build cycle:

0. create a fun custom frontend for the browser versions.
1. **Document RAG** over your own financial files (local embeddings + vector store).
2. **Function-calling tools** (compound interest, amortization, savings-goal
   calculators).
3. **Persistent memory** across sessions (local JSON/SQLite).

---

## Not financial advice

The bot is a private *thinking partner*, not a licensed financial advisor. It has no
access to your real accounts and won't invent your numbers. For big, irreversible,
or high-stakes decisions, confirm with a qualified professional.
Ha, claude wrote this^ when I said I wanted to create a fully offline bot that I could 
talk to about my personal finances. But yes, _always_ consult a human after consulting
a bot.

## Emojis
claude did _not_ add enough/any emojis so: 
🎉🎊🥳🎈🎁🎀🌟✨💫⭐🌈🔥💥⚡☀️🌙🌛🌜🌞🪐🌍🌎🌏🌊🏔️⛰️🌋🗻🏕️🏖️🏜️🏝️🌅🌄🌇🌆🏙️🌃🌌🎆🎇🌠🌉🍀🌿🍃🌾🌵🌴🌳🌲🎄🌰🍄🌻🌺🌸🌼🌷🌹🥀💐🏵️🌊🐠🐟🐬🐳🐋🦈🐙🦑🦐🦞🦀🐚🐌🦋🐛🐝🐞🦗🕷️🦂🐢🐍🦎🦖🦕🐙🦭🦦🦥🐾🐕🐈🐇🐿️ 🦫🦃🐔🐓🐣🐤🐥🦆🦢🦅🦉🦚🦜🕊️🐧🐦🦩🦨🐘🦏🦛🐪🐫🦒🦓🐂🐃🐄🐎🐖🐏🐑🦙🐐🦌🐕‍🦺🐈‍⬛🦮🐩🐾🍎🍏🍐🍊🍋🍌🫐🍈🍒🍑🥭🍍🥥🥝🍅🍆🥑🥦🥬🥒🌶️ 🫑🌽🥕🫒🧄🧅🥔🍠🥐🥯🍞🥖🥨🧀🥚🍳🧈🥞🧇🥓🥩🍗🍖🌭🍔🍟🍕🫓🥪🥙🧆🌮🌯🫔🥗🥘🫕🥫🍝🍜🍲🍛🍣🍱🥟🦪🍤🍙🍚🍘🍥🥠🥮🍢🍡🍧🍨🍦🥧🧁🍰🎂🍮🍭🍬🍫🍿🍩🍪🌰🥜🍯🥛🍼☕🫖🍵🧃🥤🧋🍶🍺🍻🥂🍷🥃🍸🍹🧉🍾🧊🥄🍴🍽️🥣🥡🥢🧂⚽🏀🏈⚾🥎🎾🏐🏉🥏🎱🪀🏓🏸🏒🏑🥍🏏🪃🥅⛳🪁🏹🎣🤿🥊🥋🎽🛹🛼🛷⛸️ 🥌🎿⛷️ 🏂🪂🏋️ 🤼🤸⛹️ 🤺🤾🏌️ 🏇🧘🏄🏊🤽🚣🧗🚵🚴🏆🥇🥈🥉🏅🎖️ 🏵️ 🎗️ 🎫🎟️ 🎪🤹🎭🩰🎨🎬🎤🎧🎼🎹🥁🎷🎺🎸🪕🎻🎲♟️🎯🎳🎮🎰🧩
