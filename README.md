# LO-FI — a local, fully-offline Pipecat voice bot

An AI voice bot that runs Key-Free & **100% offline**. Speech-to-text, the language
model, and text-to-speech are all local services; your documents never leave
your machine. Built using [Pipecat](https://github.com/pipecat-ai/pipecat)
**≥ 1.13** and [Ollama](https://ollama.com/). Its default prompts steer it
to be a private financial thinking partner: something you
can talk through money decisions with, out loud, knowing nothing you say
leaves the computer.

There is also an online version, requiring STT, LLM, and TTS service API keys.
That version is an ideal language learning partner. Read on to learn more.

---

## Requirements

- **~15 GB free disk** for the models; **16 GB+ RAM** for the default model
  pair (smaller tiers exist — see [Choosing models](#a-choosing-models-for-your-hardware)).
- **Python 3.12** (pinned; 3.14 is too new for the ML wheels).
- **[uv](https://docs.astral.sh/uv/)** — Python package manager.
- **[Ollama](https://ollama.com/) ≥ 0.40.8** — serves the local LLM.
- **Any web browser** — audio I/O and echo cancellation.

> [!NOTE]
> Re: Ollama: Older servers ignore `reasoning_effort` on `/v1` (reasoning-model `<think>` text then reaches TTS). Any install works — the menu-bar Ollama.app, brew, or the install script — but separate installs update independently, so check the server itself with `curl -s http://localhost:11434/api/version`: the one answering on 11434 is the one that counts.

---

## Quickstart

> [!NOTE]
> The first pull takes a few minutes to download the models.

```bash
git clone git@github.com:vipyne/LO-FI.git && cd LO-FI
./lofi.sh setup
bash scripts/run_ollama.sh
uv run python scripts/prefetch_models.py
```

While `run_ollama.sh` is still serving, in a second terminal:

```bash
echo "The secret passphrase for the tax vault is BLUE PELICAN 47." > data/private/test-passphrase.txt
./lofi.sh index-rag
```

Back in the first terminal: Ctrl+C; then turn off wi-fi if you want to show
off and then:

```bash
./lofi.sh start
```

Open http://localhost:7860 in any browser, click Connect & ask *"what's the
passphrase for the tax vault?"*. Neat, huh? Add more useful documents and
run `./lofi.sh stop && ./lofi.sh index-rag`.

### Help
For more configuration and options:
```bash
./lofi.sh help
```

### Bot modes cheat sheet:
- Offline
```bash
./lofi.sh --offline-only --cascade-simple-chat # simple STT,LLM,TTS bot; no tool calls
./lofi.sh --offline-only --cascade             # STT,LLM,TTS bot; tool calls available
./lofi.sh --offline-only --llmwithbackend      # front LLM for chat + back/analyst LLM for tool calls
```

- Online
```bash
./lofi.sh --online-only --cascade             # STT,LLM,TTS bot; tool calls available
./lofi.sh --online-only --llmwithbackend      # front LLM for chat + back/analyst LLM for tool calls
```

## Setup

### 1. Clone and set up

```bash
git clone git@github.com:vipyne/LO-FI.git && cd LO-FI
./lofi.sh setup
```

`setup` checks for uv and Ollama (and prints the install command when one is
missing), copies `.lofirc` and `.env` from their examples, creates
`data/private`, and runs `uv sync`. It is safe to rerun. If you skip it,
`./lofi.sh start` notices and prints `setup needed: run "./lofi.sh setup"`.

Starting from a bare Linux box or a fresh Windows WSL2 (Ubuntu) shell — no
git, no uv, no Ollama yet? This is the whole setup:

```bash
# deps
sudo apt-get update && sudo apt-get install -y git curl
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
curl -fsSL https://ollama.com/install.sh | sh

# code
git clone https://github.com/vipyne/LO-FI.git && cd LO-FI
./lofi.sh setup
```

> [!IMPORTANT]
> On WSL2, clone into the Linux filesystem — `~/`, not `/mnt/c/…`.
> Want more Windows specific instructions? See [Running on Windows](#running-on-windows)

### 2. Fetch the models (the one-time online step)

**One command fetches everything:**

```bash
uv run python scripts/prefetch_models.py
```

Approximate total download of default models: 
+ 4.7 GB front LLM 
+ 5.2 GB analyst LLM 
+ 0.3 GB embeddings (RAG)
+ 1.5 GB Whisper (STT)
+ 0.35 GB Kokoro (TTS)
=
**~12 GB**

Not sure what your machine can handle? `./lofi.sh configure` prints recommended
STT/LLM/TTS combos sized to your hardware — see
[Choosing models](#a-choosing-models-for-your-hardware).

### 3. Run

```bash
./lofi.sh start
```

Example output:
```
models:  STT     Whisper-MLX LARGE_V3_TURBO (mlx-community/whisper-large-v3-turbo)
                   → /path/to/stt/weights.safetensors   ⚠️  outside LOFI_MODEL_DIR
         LLM     qwen2.5:7b (Ollama @ http://localhost:11434/v1)
                   → /path/to/llm/.ollama/models/blobs/sha256-...   ⚠️  outside LOFI_MODEL_DIR
         ANALYST qwen3.8:27b (Ollama @ http://localhost:11434/v1)
                   → /path/to/llm/.ollama/models/blobs/sha256-...   ⚠️  outside LOFI_MODEL_DIR
         TTS     Kokoro kokoro-v1.0.onnx · voice af_heart
                   → /path/to/LO-FI/models/kokoro/kokoro-v1.0.onnx
         EMBED   nomic-embed-text (Ollama @ http://localhost:11434/v1)
                   → /path/to/LO-FI/models/sha256-...

start: bot starting (pid 91417, log → .lofi/logs/bot-20261022-205119.log)
start: stop everything lofi started with ./lofi.sh stop
start: ▶ http://localhost:7860 — allow the mic and Connect.
```

Brings up the repo-local Ollama server (if it isn't already running), prints
the exact models in play, and serves the browser bot
(`./lofi.sh stop` shuts down what it started). Open
**http://localhost:7860**, click Connect, allow the mic, talk.

### 4. Run offline

Once the models are fetched:

1. Start local Ollama server (or let `./lofi.sh start` handle it).
2. **Turn off Wi-Fi / enable Airplane Mode.** (It won't use the internet if
   you don't turn off the internet. This is just showing off.)
3. Connect and hold a conversation.

Watch the logs with:
```bash
tail -f .lofi/logs/bot-*
```

---

## Learning Objectives:
* A. learn how to evaluate a machine and what local models it can handle
* B. gain an understanding of why local audio is difficult
* C. create a basic offline voice AI chatbot
* D. add RAG to said bot to access to custom data
* E. learn how to take this local bot to the next level by going online

### A. Choosing models for your hardware

The question: *what can this computer actually run?* The process:

1. **Profile the machine.** `./lofi.sh configure` reads your RAM/CPU/GPU and prints
   STT/LLM/TTS combos sized to it (`-v` for the full report with per-slot
   model catalogs ranked by fit, `-i` to pick interactively). Everything
   resident at once has to fit — front model + analyst + embedder + STT + TTS.
   `run_ollama.sh` sets `OLLAMA_MAX_LOADED_MODELS=3` and
   `OLLAMA_KEEP_ALIVE=30m` so the working set stays loaded instead of
   thrashing.
   The suggestions are more than RAM math — the catalog is pre-filtered,
   because "biggest that fits" alone happily recommends the wrong model:
   - **Specialist weed-out, by name.** Families matching
     `code`/`math`/`sql`/`rerank`/`embed`/`guard`/`shield` (qwen3-coder,
     mathstral, sqlcoder, llama-guard…) are tuned for one job and make poor
     conversational analysts, however well they fit.
   - **Legacy weed-out, by age.** A family whose last ollama.com update is
     over a year old only ranks high on *cumulative* downloads; its own
     successors outclass it at every size.
   - **Reasoning models** are excluded from cascade picks — nothing strips
     thinking tokens before the voice (the analyst *backend* is where they
     belong).

   The filters shape suggestions, not availability: any model you set
   explicitly is honored, and `./lofi.sh configure --catalogs` (or
   `./lofi.sh status -v`) lists everything unfiltered.

2. **Budget the RAM.** Ollama's default Q4 quantization costs roughly
   **params / 2 GB of weights**, plus **1–3 GB of KV cache and runtime
   overhead** that grows with context length. Measured resident sizes on an
   M4 Pro:

   | Model | Weights (Q4) | Measured resident |
   |---|---|---|
   | 7B (`qwen2.5:7b`) | ~3.5 GB | **~4.5 GB** |
   | 8B (`qwen3:8b`) | ~4.0 GB | ~5 GB |
   | 14B (`qwen2.5:14b`) | ~7 GB | **~9 GB** |
   | 27B (`qwen3.8:27b`) | ~14 GB | **17.7 GB** |

   Add **~2 GB for STT + TTS** (Whisper-MLX large-v3-turbo plus Kokoro), and
   remember `llmwithbackend` keeps **two** LLMs resident at once — front *and*
   analyst. So the default pair is ~4.5 + 5 + 2 ≈ **11.5 GB**.

   Rule of thumb: **leave a third of your unified RAM free.** macOS and the
   browser want it, and the moment the GPU spills to swap, latency stops
   being a ranking and becomes a wait. On 16 GB that means budgeting ~11 GB —
   the defaults fit, a 14B analyst does not. On 8 GB, run `cascade` with a
   small model, or skip the weights entirely with
   [`./lofi.sh start --online-only`](#online-only-a-machine-with-no-models).

3. **Measure, don't vibe.** The eval harness is the yardstick:
   `uv run python evals/run_evals.py --stage analyst_followup` scores a
   candidate analyst on replayed production contexts (real tool calls, no
   invented facts, latency); `--stage front` checks a candidate front model
   never invents numbers and defers records questions. `evals/sweep.sh`
   loops a list of candidates through both and prints a leaderboard — edit
   the candidate lists and run it against your own hardware.

4. **Read the right numbers.** For an analyst candidate:
   `analyst_retrieval_rate` (answers came from real tool calls),
   `analyst_no_invented_fact_rate` (nothing made up), `analyst_ms_avg` (what
   the wait feels like). For a front candidate: `no_invented_numbers_rate`,
   `defers_on_data`, `answers_chitchat`. A model that's 5 points more
   accurate but twice as slow is usually the wrong trade for live voice.

Set the choice with env vars — same code:
`LOFI_ARCHITECTURE=llmwithbackend LOFI_RAG_LLM_MODEL=<tag> ./lofi.sh start`.

### B. How do you solve a problem like echo cancellation

#### Use the web browser's `getUserMedia`

The browser's audio stack ships acoustic echo cancellation, so the page at
http://localhost:7860 can use your speakers without the bot interrupting
itself. Not the internet — just the web browser, talking to the bot over
loopback.

**The browser transport follows the session mode.** An offline session is
allowed to have no network at all, and WebRTC can't connect airgapped — with
wi-fi off, ICE gathers no usable candidate (browsers exclude loopback, STUN is
unreachable). So:

| Session mode | Default transport | Why |
|---|---|---|
| `🔌 offline` | **websocket** | pure TCP to localhost — connects with wi-fi off, and crosses Windows↔WSL2 |
| `☁️ online` | **webrtc** | a network exists by definition; UDP media over loopback is the lowest-latency path |

Flipping the mode toggle reloads the page onto that mode's transport (the
toggle is pre-connect only). A failed webrtc connect falls back automatically,
once, to websocket. `?transport=` pins one transport for the whole session and
outranks the mode:

- **WebSocket** (`?transport=websocket`, *offline default*): pure TCP, works
  in every browser and across every boundary (including Windows↔WSL2, where
  UDP never crosses). Pin this one on WSL2 so online sessions skip the webrtc
  attempt.
- **SmallWebRTC** (`?transport=webrtc`, *online default*): UDP media over
  loopback, in the base install (aiortc ships a pure-Python wheel). Needs UDP
  to reach the bot — so not across Windows↔WSL2, and not with wi-fi off.

#### Use headphones (the CLI front-end)

Native macOS AEC with PyAudio is much closer to impossible than impractical,
so the no-browser front-end (`bot.py`, mic + speakers via PyAudio) needs
headphones to keep the bot from interrupting itself. PyAudio has no
macOS/Linux wheels, so it is an opt-in extra:

```bash
brew install portaudio            # Debian: sudo apt install portaudio19-dev
uv sync --extra local-audio       # the extra adds PyAudio (needs PortAudio)
./lofi.sh start -t headphones
```

> [!NOTE]
> `uv sync` uninstalls any extra you don't pass, so keep listing the ones you
> want: `uv sync --extra local-audio --extra piper`. (`./lofi.sh configure -i`
> preserves whatever is already installed.)

### C. See [above](#3-run)

### D. Retrieval (your transactions and documents)

The bot answers real questions about your own money — fully offline, no
vector database server. `data/` holds the documents, and the split is the
privacy boundary:

- **`data/private/`** — your transactions and notes (`.csv`, `.txt`, `.md`,
  `.pdf`). Offline sessions only.
- **`data/public/`** — documents you don't mind sending to a cloud service.
  The repo ships IRS Pub 17 and Pub 334 here.

```bash
./lofi.sh index-rag
```

builds `models/rag-index/lofi.db` from **all** of `data/` — the offline
session's index — and, with `OPENAI_API_KEY` set, a second database
`lofi-public.db` from `data/public/` alone, the only one an online session
can open. Put a file in the wrong directory and you've moved the boundary —
that is the whole mechanism. After adding or editing documents, re-run
`./lofi.sh index-rag`; with no index the bot starts without the tool and
simply converses.

#### The tools

Chunk-and-embed similarity search alone cannot answer "how much did I spend
on groceries?" — the grocery rows scatter across chunks and nothing adds them
up. Structured data wants typed queries, so retrieval is four tools over
SQLite, and all arithmetic happens in SQL. The model never writes SQL itself —
each tool takes whitelisted arguments and builds a parameterized query, capped
at five rows or chunks per result:

| Tool | For |
|---|---|
| `lookup` | "what did I pay Harborview?" — transaction id, or bm25 full-text over rows |
| `filter_rows` | "my five biggest expenses in March 2025" — column/operator/value filters, ordered |
| `aggregate` | "how much did I spend on groceries in 2025" — count/sum/avg/min/max, transfers excluded unless asked |
| `semantic_search` | "what's in my tax notes?" — embedding KNN fused with full-text, over everything |

#### The indexes

Each database holds typed transaction rows, an FTS5 full-text index, and a
`sqlite-vec` vector table side by side. The two builds are independent: a
failing public build exits nonzero and leaves `lofi.db` untouched. Ingest is
incremental — each file is hashed, unchanged files are skipped, edited files
are re-embedded alone, deleted files are purged. Change the embedding model
and it rebuilds from scratch and says why.

CSV files are indexed row by row, and each row is also written out as a prose
sentence — `On February 5, 2025, spent $412.00 at Harborview Credit Union
(Auto Payment) from account VIP Plus Banking (...4821).` — so full-text and
embedding search hit the same rows the typed columns do. Monthly per-category
rollups are generated alongside, so "what did March look like" has something
to retrieve. Bank statement exports work as-is.

`./lofi.sh status` shows both indexes and every model tag with its real path.

#### Is it really reading my documents? The canary trick

Plant a fact the model cannot possibly know, and ask for it:

```bash
echo "The secret passphrase for the tax vault is BLUE PELICAN 47." > data/private/canary.txt
./lofi.sh index-rag
./lofi.sh start
```

Ask *"what's the passphrase for the tax vault?"* — a correct answer can only
have come from retrieval, and the retrieved-context panel prints one line per
tool call (which tool, its arguments, how many rows came back, how long it
took) plus the composed answer. The same canary also demonstrates the index
lifecycle: delete `data/private/canary.txt` and ask again without re-indexing —
the bot still answers, because it reads the built index, never `data/`
itself. Re-run `./lofi.sh index-rag` and ask a third time — now the
passphrase is gone. Moral: the index only changes when you rebuild it.

For a negative control, `mv models/rag-index /tmp/` and restart: the bot
starts with no tool and answers ungrounded. To inspect the index directly,
`uv run python ingest.py stats`, or open `models/rag-index/lofi.db` with any
`sqlite3` client.

#### Reference documents

`data/public/` ships two IRS publications so `semantic_search` has real tax
prose to retrieve next to your transactions:

- [Publication 17](https://www.irs.gov/pub/irs-pdf/p17.pdf) — Your Federal Income Tax (individuals)
- [Publication 334](https://www.irs.gov/pub/irs-pdf/p334.pdf) — Tax Guide for Small Business

Canonical sources for more:

- IRS forms, instructions, and publications: <https://www.irs.gov/forms-instructions>
- The Internal Revenue Code itself (26 U.S.C.): <https://uscode.house.gov/download/download.shtml>
- Treasury regulations (26 CFR): <https://www.ecfr.gov/current/title-26>

Plain-prose publications chunk well. Raw statute and regulation text does
not — deeply nested numbering scatters each rule across chunks — so `data/`
carries pubs only. Drop in whatever reference documents fit your life
(leases, manuals, insurance policies) and re-run `./lofi.sh index-rag`.

### E. Online sessions

Offline is the point of this repo. Online mode exists for the two things a
local 7B on a laptop can't do well: answer a tax question in about two
seconds, and/or hold a **two-way** conversation in another language. You pick per
session, in the browser, before you Connect — the `🔌 offline` / `☁️ online`
control sits next to the Connect button and is disabled while connected. The
choice round-trips through the URL (`?mode=online`), so a reload keeps it.

| | 🔌 offline *(default)* | ☁️ online |
|---|---|---|
| Speech-to-text | Whisper-MLX / faster-whisper, local | Deepgram `nova-3` (cloud), `language=multi` |
| Language model | `qwen2.5:7b` front + `qwen3:8b` analyst, Ollama | `gpt-4o-mini` (OpenAI cloud) |
| Text-to-speech | Kokoro, local | Cartesia (cloud) |
| Embeddings | `nomic-embed-text`, Ollama | `text-embedding-3-small` (OpenAI cloud) |
| Retrieves from | **everything** — `models/rag-index/lofi.db` | **public documents only** — `models/rag-index/lofi-public.db` |
| Tools | `lookup`, `filter_rows`, `aggregate`, `semantic_search` | `semantic_search` only |
| Keys needed | none | all three in `.env` |

**The privacy boundary is the filesystem, not a prompt.** Online code never
constructs the private path: it is wired to `config.public_rag_db_path()` and
nothing else, so a private answer isn't refused by instruction, it is
unreachable. Ask an online session for your passphrase and it says the
records aren't part of this session and an offline one can answer.

What *does* leave the machine in an online session: everything you say, and
every chunk of public document the retrieval pulls in. The page says so, in a
red `⚠️ ONLINE MODE` banner you can read from the back row, and the What's
Running panel lists each cloud service by name.

#### Setting it up

```bash
cp env.example .env
```

Fill in `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`, then build
the public index (the offline build runs first, then the public one over
`data/public/` through OpenAI embeddings):

```bash
./lofi.sh index-rag
```

With no `OPENAI_API_KEY` the public build is skipped with a one-line note and
the offline index still builds. Connecting online with a key missing fails at
connect and names **every** key that's absent. With no public index, the
online session still converses — it just has no documents to cite, and the
What's Running panel says so.

#### Online-only: a machine with no models

```bash
./lofi.sh start --online-only
```

Serves the browser bot with `LOFI_ONLINE_ONLY=1`: no Ollama start, no model
wait, no local STT/TTS preflight. Useful on a machine that never downloaded
the ~12 GB of weights, or when Ollama is deliberately stopped. A bare connect
in that process defaults to online. It refuses `-t headphones` — the
local-audio bot has no online stack.

#### Offline-only: nothing leaves the machine

```bash
./lofi.sh start --offline-only
```

The mirror flag: serves with `LOFI_OFFLINE_ONLY=1`. The local stack runs as
usual, and an online connect is refused. Either restriction can also live in
`.lofirc` (`LOFI_ONLINE_ONLY=1` / `LOFI_OFFLINE_ONLY=1`); the start flags win
over the file, both unset means both modes are available, and when both vars
are set offline-only wins. The page greys out the mode the process refuses
and pre-selects the one it serves.

---

## Configuration

Offline configuration lives in `.lofirc`.
Online-session API keys live in `.env`.

### Two architectures; `Cascade` & `LLMWithBackend`

`LOFI_ARCHITECTURE` picks how retrieval and conversation share the work —
set it in `.lofirc`, or per start with the `./lofi.sh start --cascade` /
`--llmwithbackend` flags (flags win over the file):

- **`llmwithbackend`** (the default) — a conversational front model (reasoning off)
  delegates records work to a reasoning analyst backend via Pipecat's
  `LLMWithBackend`/`BackendLLMWorker`. The front holds one domain-named
  builtin (`ask_financial_records`); the backend (`LOFI_RAG_LLM_MODEL`,
  reasoning on, native API) holds the four retrieval tools and answers from
  your records. The backend's thinking stays in the native API's `reasoning`
  field and never reaches TTS; its answers are written back into the
  conversation, so follow-ups work.
- **`cascade`** — one model does the talking and the retrieving. It holds the
  four typed tools over the index; any question about transactions, spending,
  merchants, accounts, totals, or saved documents turns into tool calls, and
  the model answers only from what they return — with a short "Let me check
  your records." covering the lookup. The simplest architecture to read and
  the stable fallback for small machines.

#### Where a setting comes from

For any `LOFI_*` knob, first hit wins:

1. **The shell environment** — `LOFI_ARCHITECTURE=cascade ./lofi.sh start`.
   Beats both files, always. The start flags (`--online-only`,
   `--offline-only`, `--cascade`, `--llmwithbackend`) export into this slot,
   so they win too.
2. **`.lofirc`** — your persistent settings. Loaded as defaults, so it never
   overrides the line above.
3. **`.env`** — loaded next, same no-override rule, so `.lofirc` wins for a
   name in both. Keys and online-session config live here; anything else is
   flagged at startup with a pointer to `.lofirc`.
4. **`tunables.py`** — the default baked into the code, and the end of the
   ladder for models and voices.

**Prompts are not on this ladder.** They have no env var at all: every system
prompt, tool description and canned line lives in
[`tunables.py`](tunables.py), and editing that file IS the mechanism. Landmine
comments mark measured, byte-sensitive text.

An offline `LOFI_*` setting found in `.env` still applies, and startup prints
one line per stray name asking you to move it to `.lofirc` (online-session
names belong in `.env` and are not flagged). The same startup check
names any unprefixed settings (`STT_ENGINE`, `LLM_MODEL`, …) — the only
unprefixed names the repo reads are the four external ones below.

**`LOFI_` means it's ours.** Anything read by this repo carries the prefix, so
you can tell at a glance what's safe to change and what belongs to someone
else. Exactly four variables are **external** — read by third-party software,
keeping their upstream names because renaming them would break the tool that
reads them:

| External variable | Read by |
|---|---|
| `HF_HOME`, `HF_HUB_DISABLE_PROGRESS_BARS` | `huggingface_hub` |
| `OLLAMA_MODELS`, `OLLAMA_HOST` | the `ollama` binary |

---

## Running on Windows

Two routes:

- **Native (simplest):** PowerShell + uv, no Linux layer, full machine RAM.
- **WSL2:** the bot runs in Linux, your browser/mic/speakers stay on Windows.
  You get the bash tooling (`./lofi.sh`), at the cost of
  installing WSL and a default RAM cap.

Either way, **expect speech-to-text to be the slow part**: without Apple
Silicon, Whisper runs on the CPU. Two knobs if it drags:
`LOFI_FASTER_WHISPER_MODEL` (a smaller model than the `DISTIL_MEDIUM_EN`
default is faster and less accurate), or the tiny CPU-first engine:
`uv sync --extra moonshine` + `LOFI_STT_ENGINE=moonshine`.

### Route A: native Windows (PowerShell)

No admin needed. In PowerShell:

```powershell
# one-time tools
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
winget install --id Git.Git          # if you don't have git
winget install --id Ollama.Ollama    # the local LLM server

# code + env (uv fetches its own Python 3.12)
git clone https://github.com/vipyne/LO-FI.git
cd LO-FI
uv sync

# models (one-time, online)
uv run python scripts/prefetch_models.py

# run — the bash launcher (lofi.sh) doesn't run natively; start the bot directly
uv run python bot_browser.py --host localhost --port 7860
```

Then open **`http://localhost:7860`** in any browser, Connect, allow the mic,
talk. Env vars in PowerShell are session-scoped assignments, e.g.:

```powershell
$env:LOFI_LLM_MODEL = "llama3.2:1b"
$env:LOFI_ARCHITECTURE = "cascade"
```

Native Windows runs online sessions' `webrtc` transport without fallback:
loopback UDP works here, unlike WSL2.

### Route B: WSL2

#### 1. Install WSL2 (one-time)

Follow Microsoft's guide: <https://learn.microsoft.com/windows/wsl/install>.
The short version: open an **admin** terminal (Win+X → "Terminal (Admin)"),
run `wsl --install`, reboot, and create your Ubuntu username/password when the
first launch asks. No special networking configuration is needed — the
WebSocket transport below works in WSL2's default (NAT) networking mode.

#### 2. Set up lofi inside WSL

Open Windows Terminal → the **Ubuntu** profile (the ˅ dropdown next to the
new-tab button), then run the bare-Linux setup block from
[Setup step 1](#1-clone-install-system-deps-and-sync-the-environment) — deps
(git, curl, uv, Ollama), clone, `uv sync`. Two WSL-specific rules:

- **Clone into the Linux filesystem** (`~/`), never `/mnt/c/…` — the Windows
  mount is slow and flaky for this workload.
- Everything below runs **inside the Ubuntu tab**; only the browser runs on
  Windows.

#### 3. Pick models that fit your RAM

WSL shares the machine's RAM, and every resident model has to fit —
`./lofi.sh configure` profiles the machine and ranks model combos that fit (`-i`
to pick interactively). The default pair (qwen2.5:7b front + qwen3:8b
analyst) wants **16 GB+**. On a ~8 GB machine, drop to a single small model
in `cascade` mode before prefetching:

```bash
export LOFI_ARCHITECTURE=cascade LOFI_LLM_MODEL=llama3.2:1b LOFI_RAG_LLM_MODEL=llama3.2:1b
uv run python scripts/prefetch_models.py
./lofi.sh index-rag          # optional: build the RAG index
```

That tier is **proof-of-life, not accuracy** — it converses and exercises the
whole pipeline, but a 1B model fumbles records questions (measured: as a
front model it invents numbers on ~1 in 3 data questions, which is why the
small-machine recipe uses `cascade`, not the `llmwithbackend` default). Real
records-accuracy wants the 16 GB tier — the
[RAM budget](#a-choosing-models-for-your-hardware) shows what each step up buys.

#### 4. Run

```bash
./lofi.sh start
```

(`lofi.sh` detects WSL and skips its browser auto-open — the right browser is
on the Windows side.) Then, in **a browser on Windows**, open the page with
**`?transport=websocket`** — WebSocket is the only transport that works here:
WebRTC's media is UDP, which never crosses the Windows↔WSL boundary (verified
in NAT and mirrored modes, Hyper-V firewall open, raw-UDP probe). The WebSocket transport is pure TCP and crosses the
boundary the same way the web page itself does. Offline sessions default to
websocket anyway; an **online** session defaults to `webrtc` and falls back
to websocket on its own, but that costs ~15–30 s of failing first — so pin
the param and the mode toggle leaves the transport alone:

```
http://localhost:7860/?transport=websocket
```

Then click **Connect**, allow the mic, talk.

### Troubleshooting

- **WSL only sees about half your RAM** — that's its default cap, and it's
  what `./lofi.sh configure` and Ollama see. A 16 GB machine shows ~8 GB inside
  Ubuntu. Raise it: edit `%UserProfile%\.wslconfig` on the Windows side
  (`vi /mnt/c/Users/<you>/.wslconfig` from bash works) to

  ```ini
  [wsl2]
  memory=12GB
  ```

  then `wsl --shutdown` in PowerShell and relaunch. With 12 GB the real
  16 GB-tier model pair usually fits.
- **Forgot your Ubuntu sudo password**: in PowerShell, `wsl -d Ubuntu -u root`,
  then `passwd <your-username>`, `exit`. (A once-a-day MOTD error in the root
  shell is harmless noise.)
- **First reply after startup is slow**: that's the model loading into RAM;
  `OLLAMA_KEEP_ALIVE=30m` keeps it resident afterwards.
- **A `(could not resolve models — run 'uv sync' and retry)` line at start**:
  cosmetic; the bot still starts.
- **Editing Windows-side files from bash**: they're under `/mnt/c/Users/<you>/…`
  (e.g. `vi /mnt/c/Users/<you>/.wslconfig`) — handy, but keep the repo itself
  in `~/`.

## Intel Macs

Use [`intel-mac` branch](../../tree/intel-mac).

## Repository layout

Both bots share one brain (`pipeline.py` — the same STT → VAD → LLM → TTS
pipeline, built offline or online from the session's mode); they differ only
in the transport. The STT/LLM/TTS services are built in `services.py`,
dispatched on `LOFI_STT_ENGINE` / `LOFI_TTS_ENGINE` — so swapping engines
(via `.lofirc` or `./lofi.sh configure -i`) never touches a bot file you may have
customized.

```
lofi/
├── bot_browser.py            # the browser bot — serves client/dist, picks the session's stack
├── bot.py                    # the headphones/CLI bot — PyAudio in and out
├── pipeline.py               # the one build_pipeline() every bot shares
├── services.py               # STT/LLM/TTS builders, engine-dispatched
├── tunables.py               # every prompt + model defaults — THE file to edit
├── config.py                 # env-driven settings, zero-config defaults
├── retrieval.py              # lookup / filter_rows / aggregate / semantic_search
├── ingest.py                 # extract → serialize → chunk → embed → index
├── lofi.sh                   # start | stop | status | index-rag | consolidate
├── configure.sh              # what can this machine handle?
├── data/                     # private/ (offline only) + public/ (online's whole world)
├── client/                   # the browser UI (Vite + TypeScript; prebuilt in client/dist)
└── models/                   # ALL checkpoints — $LOFI_MODEL_DIR, relocatable, gitignored
```

---

## Not financial advice

The bot is a private *thinking partner*, not a licensed financial advisor. It
has no access to your real accounts, only the documents you give it access
to. Always, _always_ consult a human after consulting a bot, in all matters,
financial or otherwise.

## Emojis
claude did _not_ add enough/any emojis so: 
🎉🎊🥳🎈🎁🎀🌟✨💫⭐🌈🔥💥⚡☀️🌙🌛🌜🌞🪐🌍🌎🌏🌊🏔️⛰️🌋🗻🏕️🏖️🏜️🏝️🌅🌄🌇🌆🏙️🌃🌌🎆🎇🌠🌉🍀🌿🍃🌾🌵🌴🌳🌲🎄🌰🍄🌻🌺🌸🌼🌷🌹🥀💐🏵️🌊🐠🐟🐬🐳🐋🦈🐙🦑🦐🦞🦀🐚🐌🦋🐛🐝🐞🦗🕷️🦂🐢🐍🦎🦖🦕🐙🦭🦦🦥🐾🐕🐈🐇🐿️ 🦫🦃🐔🐓🐣🐤🐥🦆🦢🦅🦉🦚🦜🕊️🐧🐦🦩🦨🐘🦏🦛🐪🐫🦒🦓🐂🐃🐄🐎🐖🐏🐑🦙🐐🦌🐕‍🦺🐈‍⬛🦮🐩🐾🍎🍏🍐🍊🍋🍌🫐🍈🍒🍑🥭🍍🥥🥝🍅🍆🥑🥦🥬🥒🌶️ 🫑🌽🥕🫒🧄🧅🥔🍠🥐🥯🍞🥖🥨🧀🥚🍳🧈🥞🧇🥓🥩🍗🍖🌭🍔🍟🍕🫓🥪🥙🧆🌮🌯🫔🥗🥘🫕🥫🍝🍜🍲🍛🍣🍱🥟🦪🍤🍙🍚🍘🍥🥠🥮🍢🍡🍧🍨🍦🥧🧁🍰🎂🍮🍭🍬🍫🍿🍩🍪🌰🥜🍯🥛🍼☕🫖🍵🧃🥤🧋🍶🍺🍻🥂🍷🥃🍸🍹🧉🍾🧊🥄🍴🍽️🥣🥡🥢🧂⚽🏀🏈⚾🥎🎾🏐🏉🥏🎱🪀🏓🏸🏒🏑🥍🏏🪃🥅⛳🪁🏹🎣🤿🥊🥋🎽🛹🛼🛷⛸️ 🥌🎿⛷️ 🏂🪂🏋️ 🤼🤸⛹️ 🤺🤾🏌️ 🏇🧘🏄🏊🤽🚣🧗🚵🚴🏆🥇🥈🥉🏅🎖️ 🏵️ 🎗️ 🎫🎟️ 🎪🤹🎭🩰🎨🎬🎤🎧🎼🎹🥁🎷🎸🪕🎻🎲♟️🎯🎳🎮🎰🧩
