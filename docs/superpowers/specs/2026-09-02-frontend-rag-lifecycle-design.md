# locat: custom frontend, RAG, lifecycle — design

Date: 2026-09-02
Status: approved in discussion; this document is the written record.

## Goals

1. A hand-rolled browser frontend over MoQ transport (fast; browser gives echo
   cancellation for free via getUserMedia) with voice, text input, and full
   visibility into what the bot is running.
2. Local RAG over the user's own text/pdf files, fully offline, hand-rolled and
   readable top to bottom.
3. Crystal-clear start/stop/status lifecycle — especially ollama ownership
   (locat-started vs. user's own instance).
4. Everything keeps working 100% offline after models are downloaded, on
   mac/linux/windows-via-WSL2.

Secondary goal throughout: demystification. Models always shown with full file
paths; no framework magic; retrieval visible in the UI as it happens.

## Non-goals (this milestone)

- Native Windows (WSL2 is the documented path; headphones/PyAudio mode is
  excluded on Windows — browser mode covers it).
- Incremental re-indexing (designed for, not built — see manifest.json).
- Function-calling tools, persistent memory (later roadmap items).
- Relay-based MoQ deployment (serve mode only; no relay ever).
- Zip-and-go portability is a stretch goal: documented honestly, not engineered.

## Architecture

```
locat/
├── locat            NEW  single lifecycle command (bash): start|stop|status|index|configure|models
├── pipeline.py      NEW  shared build_pipeline() used by both bots
├── rag.py           NEW  index(data_dir) + retrieve(query, k) — the only RAG surface
├── client/          NEW  vanilla-TS frontend (Vite; dist/ committed)
├── data/            NEW  user documents (gitignored)
├── bot.py           kept: headphones / system-audio transport glue
├── bot_moq.py       kept: MoQ serve-mode transport glue
├── bot_web.py       DELETED (webrtc dropped entirely)
├── configure.sh     RENAMED from doctor.sh (same behavior)
└── services.py, config.py, ...   unchanged roles
```

Transports: **MoQ serve mode** (bot binds its own QUIC socket, self-signed
cert, browser pins it via base64 certHash — no relay) and **headphones**
(LocalAudioTransport). The webrtc bot, the `webrtc` extra, and the
`pipecat-ai-prebuilt` dependency are removed.

Pre-refactor (Phase 0): the pipeline list is duplicated verbatim across bot
files. Extract `build_pipeline(transport)` (VAD → STT → user agg → [RAG] → LLM
→ TTS → output → assistant agg) into `pipeline.py`; bot files shrink to
transport construction + runner glue.

## Frontend (`client/`)

Vanilla TypeScript + Vite. No UI framework. Runtime deps only
`@pipecat-ai/client-js` and `@pipecat-ai/moq-transport` — the transport owns
getUserMedia (echo cancellation on), Opus encode, AudioWorklet jitter-buffer
playback, WebTransport connection, and the bidirectional RTVI text track
(`transcript.json.z`), so typed text input needs no extra channel.

`client/dist/` is **committed**: running the bot never requires Node. Node +
`npm run build` only for frontend development. `npm run dev` proxies to the
bot's HTTP server.

UI elements:
- Connect / disconnect, mic mute, plain-language connection state.
- Transcript pane: user turns (spoken and typed) + streamed bot responses via
  RTVI events.
- Text input → RTVI sendMessage over the MoQ text track.
- **What's-running panel**: on connect the server sends its exact config (STT/
  LLM/TTS engines, model names, full file paths, ollama host, embed model) as
  an RTVI server message; client renders it verbatim.
- **Retrieved-context panel**: per turn, the server pushes the RAG chunks it
  injected (source path, page, similarity score); the client shows them live.
- WebTransport feature-detect with a plain-language unsupported-browser notice.

Server side of the frontend:
- Serve `client/dist/` from the bot's HTTP server. Confirm at implementation
  whether the pipecat dev runner can mount a custom static dir; otherwise a
  ~10-line static mount.
- Port the **audio-subscriber gate** from pipecat-moq-example
  (`wait_for_audio_subscriber`): RTVI client-ready can arrive before the
  browser subscribes to the bot's audio track; MoQ is live media with no
  replay, so the greeting would be silently dropped. Await the audio
  producer's first subscriber (with timeout) before greeting.

Confirm at implementation:
- Whether @moq/net's WebSocket fallback works against Python serve mode (no
  relay). If not: WebTransport-only, README names the supported browsers.
- Exact RTVI server-message API in pipecat 1.7 for the two custom panels.

## RAG (`rag.py`)

Hand-rolled, always-on retrieval, behind a hard two-function boundary so the
internals can be swapped (sqlite-vec, better PDF extraction, a framework)
without touching bot code:

```python
def index(data_dir: Path) -> IndexStats
def retrieve(query: str, k: int) -> list[Chunk]   # Chunk: text, source_path, page, score
```

- **Data**: `data/` in-repo, gitignored; override with `LOCAT_RAG_DATA_DIR`.
  Formats: `.txt`, `.md`, `.pdf` (pypdf — pure Python, no wheel risk; known
  weakness on table-heavy PDFs, first candidate for upgrade).
- **Index** (`./locat index`): walk → extract → chunk (~500 tokens, ~50
  overlap, sentence-boundary-aware) → embed via **Ollama `/api/embed`** with
  `nomic-embed-text` (~270 MB; pulled/stored/shown by configure.sh exactly
  like the LLM). Output in `$LOCAT_MODEL_DIR/rag-index/`:
  - `chunks.jsonl` — {text, source_path, page}
  - `embeddings.npy` — one row per chunk
  - `manifest.json` — file hashes + embed model name. v1 rebuilds fully when
    anything changed; the manifest makes incremental indexing a drop-in later.
- **Runtime**: a small pipecat processor after the user aggregator: embed the
  final transcript, numpy cosine top-k, inject chunks into LLM context, log
  them with full source paths, push them to the client panel. Budget 20–50 ms.
- **Graceful degradation**: no index → bot runs normally, logs
  "no RAG index found — run ./locat index". Preflight checks the embed model
  the same way `_preflight_llm` checks the LLM.

New env vars (all optional, sane defaults): `LOCAT_RAG_DATA_DIR` (`data/`),
`LOCAT_EMBED_MODEL` (`nomic-embed-text`), `LOCAT_RAG_TOP_K` (4),
`LOCAT_RAG_CHUNK_TOKENS` (500), `LOCAT_RAG_CHUNK_OVERLAP` (50).

## Lifecycle (`./locat`)

One entry point, subcommands:

- `start [-t moq|headphones]` — starts ollama only if not already running;
  records the PID of any ollama **it** started in `.locat/ollama.pid`; records
  bot PID in `.locat/bot.pid`. default is `moq`. open the localhost address
  for the user as well.
- `stop` — kills only PIDs locat recorded. Never pkills a user's own ollama.
  Never closes the browser tab.
- `status` — states ownership in words:
  ```
  ollama   running (pid 4242), started by locat, store: models/ollama
           — or — running, NOT started by locat (your own instance; locat stop leaves it alone)
  bot      running (pid 4310), transport: moq, http://localhost:7860
  stt/llm/tts/embed  model names + full file paths
  rag      index: 1,204 chunks from 17 files (data/), embed: nomic-embed-text
           — or — no index (run ./locat index)
  ```
- `index-rag` — build/rebuild the RAG index.
- `configure` — delegates to `configure.sh` (renamed doctor.sh).
- `models` — delegates to scripts/print_models.py.

remove `start.sh` / `stop.sh`. command to start is `locat.sh start` and
`locat.sh stop`.
`.locat/` is gitignored.

## Testing

- pytest: rag chunking/indexing/retrieval with a fake embedder + tiny fixture
  docs (no ollama in unit tests; one integration test uses ollama when up),
  locat status/pidfile logic, subscriber-gate logic (as in the moq-example).
- Frontend: `npm run build` (tsc + vite) must pass; committed dist/ must be
  current.
- Anything requiring ears/mic is a human phase gate, never auto-verified.

## Ralph loop (phased)

Reuse the existing `ralph/` harness. Changes:
- Delete the v1 run (old PLAN.md, PROGRESS.md, logs).
- **Phase gates**: when a phase's tasks are all verified, the iteration writes
  `ralph/RALPH_PHASE_DONE.md` (what was built, how to review, what manual test
  to run) + empty sentinel `ralph/RALPH_PHASE_DONE`, commits, stops. ralph.sh
  exits on the sentinel. Human reviews/tests, deletes the sentinel, re-runs.
- Same rules otherwise: fresh amnesiac iteration, one task, PROGRESS.md is
  memory, verify-before-checkoff, context-hub for concepts but installed
  source wins, never fake human-gated verifications.

Phases (● = human gate at end, ○ = rolls on):

- **Phase 0** ○ — pipeline.py refactor; drop webrtc (bot_web.py, extra,
  prebuilt dep); doctor.sh → configure.sh rename; `./locat` command with
  pidfile ownership + status + shims. Verify: both bots construct, tests pass.
- **Phase 1** ○ — rag.py core + `locat index` (chunking, pypdf, embedding,
  store, retrieve) with pytest fixtures.
- **Phase 2** ● — RAG wired into pipeline: processor, injection, logging, RTVI
  push, configure.sh/env.example/README updates. Gate: real docs in data/,
  index, voice conversation that cites them.
- **Phase 3** ● — frontend scaffold: Vite project, connect + audio over MoQ
  serve mode, subscriber gate, static serving. Gate: talk through the new page.
- **Phase 4** ● — frontend features: text input, transcript, what's-running
  panel, retrieved-context panel, styling, WebTransport detection.
  Gate: full walkthrough.
- **Phase 5** ● — README rewrite (lifecycle, RAG, frontend, WSL2 story),
  offline verification checklist, zip-and-go notes. Gate: airplane-mode
  end-to-end test.
