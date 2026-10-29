# locat v2 Implementation Plan

> **For agentic workers:** Executed by a ralph loop (`ralph/ralph.sh` +
> `ralph/PROMPT.md`), one task per iteration. Tick checkboxes in this file as you
> go; keep the Status table below current every iteration.

## Status
<!-- The loop refreshes this table and the Updated line EVERY iteration. -->
| # | Task | Status | Model |
|---|------|--------|-------|
| 1 | Shared pipeline, drop webrtc, `locat.sh` | done | — |
| 2 | `rag.py` core — chunk, extract, embed, index, retrieve | done | — |
| 3 | RAG in the pipeline + RTVI visibility messages | done | — |
| 4 | Frontend scaffold — connect + audio over MoQ | done | — |
| 5 | Frontend features — transcript, text input, panels, polish | done | — |
| 6 | Never speak thinking blocks | pending | opus |
| 7 | File upload button | gated: HUMAN.md §3 | — |
| 8 | voice-ui-kit variant alongside the hand-rolled client | pending | opus |
| 9 | README rewrite | gated: HUMAN.md §4 | sonnet |
| 10 | Offline checklist + zip-and-go notes | pending | sonnet |

_Updated: 2026-09-15, iteration 0 (converted to the create-ralph-loop format)_

**Goal:** locat is a voice bot that runs 100% offline. v2 adds three things to the working v1:
a custom browser frontend over MoQ transport (voice + typed text), fully-offline RAG over the
user's own documents, and a single `locat.sh` lifecycle command that makes Ollama ownership
unambiguous. Done looks like: airplane mode on, `./locat.sh start`, a spoken and typed
conversation that cites the user's own documents, with the UI showing exactly which models
are loaded from which paths and which chunks were retrieved — and no reasoning text ever
spoken aloud.

**Tech stack:** Python 3.12 (pinned), `uv`; pipecat-ai 1.7.0 (`pipecat-ai[kokoro,moq,silero,whisper]`);
Whisper-MLX STT / Ollama LLM / Kokoro TTS / `nomic-embed-text` embeddings; MoQ serve mode
(`moq_rs` 0.3.3, no relay); `pypdf` + numpy for RAG; Vite + vanilla TS in `client/`
(plus React/Tailwind only inside Task 8's variant); `fastapi`/`uvicorn` serve `client/dist`.

**Source plan/spec:** `docs/superpowers/specs/2026-09-02-frontend-rag-lifecycle-design.md`

## Global Constraints

- **Never push, never `gh`.** Commits stay local; the human reviews and pushes (HUMAN.md §1).
- **Never rewrite git history** — no `rebase`, `reset`, `clean`. (A rebase deleted this whole
  directory once; the deny list blocks these.)
- **`uv` for all Python** (`uv sync`, `uv run`, `uv add`) — never bare pip/venv. `npm` only
  inside `client/`.
- **Never run bare `uv sync`** — it uninstalls opt-in extras (it silently dropped `pyaudio`
  in Task 1). Always name them: `uv sync --extra local-audio`.
- **Any change under `client/src/` rebuilds `client/dist/` in the same iteration**
  (`cd client && npm run build`) and commits it — `dist/` is committed and must never lag src.
- **Minimal comments** — fix the name instead; explanations belong in the README. Config files
  stay scannable.
- **One README per repo, top level only.** Other docs get non-README names.
- **New env vars are `LOCAT_*`**, read via `config._get()`, documented in `env.example` and the
  README config table.
- **Full file paths in anything user-facing** — models are always shown with the real path they
  load from. That demystification is a product goal, not a nicety.
- **Byte sizes: never label a binary number with a decimal unit** (dividing by 1024 and printing
  "GB"). `2^30` → print `GiB`; `1e9` → print `GB`. Within one column every value uses the same
  ruler: `ollama list`, `scripts/fetch_catalog.py` and `configure.sh`'s catalog are decimal GB;
  `status`'s downloaded inventory is GiB and says so.
- **Models are used from wherever they already exist** on the machine (`HF_HOME` or the home
  cache). `./locat.sh consolidate` is a convenience for browsing and portability — never a
  required step, never a remedy the user must run to make the bot work.
- **Never guess APIs.** Pipecat: `pipecat-context-hub` MCP for concepts, then verify exact names
  against installed source in `.venv/lib/python3.12/site-packages/pipecat/` — installed wins
  when they disagree, and prefer non-deprecated symbols. JS: read the installed `.d.ts` under
  `client/node_modules/@pipecat-ai/`.
- **Reference implementation** (read-only, on this machine):
  `/Users/vipyned2/Documents/repos/vipyne/pipecat-moq-example` — a working MoQ client + bot.

---

### Task 1: Shared pipeline, drop webrtc, `locat.sh` — done

**Files:** `pipeline.py`, `locat.sh`, `configure.sh` (renamed from `doctor.sh`),
`tests/test_locat_sh.py`; deleted `bot_web.py`, `start.sh`, `stop.sh`.

- [x] **Step 1: extract `pipeline.py`** — `BuiltBot` dataclass + `build_pipeline(transport)`;
      the VADProcessor moved here too. Aggregators verified against installed 1.7 source:
      `LLMUserAggregator` / `LLMAssistantAggregator` from
      `pipecat.processors.aggregators.llm_response_universal`.
- [x] **Step 2: delete webrtc** — `bot_web.py`, the `webrtc` extra, the `-t webrtc` branch.
- [x] **Step 3: rename `doctor.sh` → `configure.sh`** — all references; the preview env vars
      became `LOCAT_CONFIGURE_*`.
- [x] **Step 4: `locat.sh` lifecycle command** — pidfile ownership in `$LOCAT_STATE_DIR`
      (default `.locat/`); `stop` kills ONLY recorded pids and never pkills; a foreign Ollama is
      reported and left alone.
- [x] **Step 5: lifecycle tests** — `tests/test_locat_sh.py` (ownership, stale pidfile cleanup,
      foreign-Ollama status). pytest became a dev dependency here.

Later additions (built outside the loop, keep them):
per-run session logs (`.locat/logs/bot-<ts>.log`, newest 10 kept, `.locat/bot.log` symlinks the
newest); `get-debug-log [n]`; per-command help (`./locat.sh <cmd> help`); a `LOCAT_ENV_FILE`
override (tests point it at `/dev/null`); `consolidate [-n]`; `status` opening with a machine
section (`configure.sh --hardware`) and closing with a `downloaded` inventory
(`print_models.py -d`), plus `status -v` for the full untrimmed catalogs
(`configure.sh --catalogs`). **`./locat.sh models` and `configure -a` are REMOVED** —
`scripts/print_models.py` survives as internal plumbing (`--bare`, `-d`).

---

### Task 2: `rag.py` core — chunk, extract, embed, index, retrieve — done

**Files:** `rag.py`, `scripts/make_test_pdf.py`, `tests/test_rag.py`, `tests/fixtures/`.

- [x] **Step 1: `chunk_text`** — sentence-packing to a token budget (whitespace-word
      approximation), tail-word overlap, hard word-split for over-long sentences.
- [x] **Step 2: `extract`** — `.txt`/`.md` (one `PageText`), `.pdf` via pypdf (one per page),
      unknown extensions skipped with a log. The PDF fixture is generated by a hand-written
      `scripts/make_test_pdf.py` so it stays reproducible. **`.csv` added later**: first row is
      the header, each data row renders as `header: value, …` ending in a period so chunking
      never splits a row.
- [x] **Step 3: embedders** — `Embedder` Protocol, `OllamaEmbedder` (httpx POST to
      `/api/embed`), `FakeEmbedder` (deterministic sha256-seeded vectors for tests).
- [x] **Step 4: `index()` / `retrieve()`** — full rebuild writes `chunks.jsonl` +
      `embeddings.npy` + `manifest.json`; retrieval is cosine top-k with a module-level cache
      keyed on index mtime; a missing index returns `[]`.
- [x] **Step 5: CLI + wiring** — `rag.py index` / `rag.py stats --bare`, embed-model preflight,
      `locat.sh index-rag`, the status rag line, and the `LOCAT_RAG_*` config getters.

---

### Task 3: RAG in the pipeline + RTVI visibility messages — done

**Files:** `rag_processor.py`, `pipeline.py`, `bot.py`, `bot_moq.py`, `scripts/print_models.py`,
`configure.sh`, `tests/test_rag_processor.py`, `tests/test_pipeline_rag.py`.

- [x] **Step 1: `rag_processor.py`** — intercepts `LLMContextFrame`, retrieves via
      `asyncio.to_thread`, and maintains exactly ONE excerpts system message in the context
      (replaced each turn, never accumulated). **The excerpts message is inserted BEFORE the
      latest user message, never appended after it** — a context ending on a system turn makes
      small llama models echo a literal `assistant` header before their reply (A/B-verified
      against `llama3.2:1b`). Regression test:
      `test_excerpts_inserted_before_latest_user_message_never_trailing`.
- [x] **Step 2: wire into `build_pipeline`** — `pipeline.build_rag_processor()` returns a
      RAGProcessor with an `OllamaEmbedder` when `rag.has_index()`, else `None` plus a one-time
      INFO log; spliced between the user aggregator and the LLM.
- [x] **Step 3: STT preflight, reworked** — weights are used from whatever cache holds them
      (`HF_HOME` or the home cache); the service receives a resolved local snapshot path when
      the weights live outside `HF_HOME`; preflight fails only when NO cache has them, and the
      remedy is `scripts/prefetch_models.py`. (The first version wrongly required
      `consolidate`, which is why the constraint above exists: a gate found the bot "deaf"
      because a silent 1.6 GB HF download fired on every utterance.)
- [x] **Step 4: RTVI visibility** — `bot_moq.py` sends `locat-config` on client-ready via
      `worker.rtvi` (engines, model names, full weight paths, Ollama host + ownership, rag
      stats); `rag_processor` emits `locat-rag` per retrieval. `print_models.model_entries()`
      and `rag.stats_summary()` exist so the CLI and the RTVI message read one source.
- [x] **Step 5: configure.sh + docs** — the EMBED model line in print_models / configure.sh /
      `status`; `configure.sh -i` offers to pull it when missing; README gained a
      "RAG (documents)" section (plus a canary-trick subsection later).

---

### Task 4: Frontend scaffold — connect + audio over MoQ — done

**Files:** `client/` (Vite + vanilla TS), `client/src/connection.ts`, `bot_moq.py`.

- [x] **Step 1: Vite scaffold** — vanilla TS, Connect button + connection-state line,
      `dist/` committed, `base: "./"`, dev proxy for `/start`.
- [x] **Step 2: serve `client/dist` from the runner app** — `pipecat-ai-prebuilt` dropped;
      `fastapi` and `uvicorn` became direct dependencies as a result.
- [x] **Step 3: connect + audio** — `connection.ts` POSTs `/start`, builds `MoqTransport` from
      the returned `moq` block, drives the client-js client; `locat.sh` passes `-t moq`.
- [x] **Step 4: audio-subscriber gate** — the moq-example's `producer.used()` does NOT exist in
      `moq_rs` 0.3.3, so the gate is track-open + client-ready based, bounded by
      `LOCAT_MOQ_SUBSCRIBER_TIMEOUT` (default 15s). Also fixed: the Connect button rendered as
      "Disconnect" on load because `initialized`/`initializing` were treated as connected.
- [x] **Step 5: un-clip bot turn starts** — the first word of each turn was clipped. Fix:
      COOP/COEP response headers so the page can use `SharedArrayBuffer` for the audio ring,
      plus a write-ahead cap (`LOCAT_MOQ_AUDIO_AHEAD_MS`, default 500).

---

### Task 5: Frontend features — transcript, text input, panels, polish — done

**Files:** `client/src/transcript.ts`, `client/src/panels.ts`, `client/index.html`,
`client/src/style.css`, `config.py`.

- [x] **Step 1: transcript pane** — `userTranscript` / `botOutput` / `botLlmStarted` via
      `client.on`; user rows stream italic while partial and commit on final; bot rows stream
      sentence-by-sentence keyed on `segment_id`; auto-scroll pins to the bottom only when you
      are already there.
- [x] **Step 2: text input** — `#composer` → `client.sendText`, instant local user row via
      `addUserText`, disabled while idle. Typing interrupts the bot and gets a spoken reply.
- [x] **Step 3: what's-running panel** — `panels.ts` renders `locat-config` verbatim in
      monospace: engines, model names, full weight paths, Ollama host, ownership line. Needed a
      new `config.state_dir()` so the bot can report `ollama_started_by_locat`.
- [x] **Step 4: retrieved-context panel** — `attachRagPanel` on `locat-rag`; newest retrieval on
      top, italic query then `source_path p.N (score)` rows expanding via native `<details>`;
      static empty state when there is no index.
- [x] **Step 5: polish** — Mute/Unmute via `enableMic`/`isMicEnabled`; a banner plus disabled
      Connect when the browser lacks WebTransport; two-column grid (transcript + composer left,
      panels right, controls in the header) collapsing to one column on narrow windows.

---

### Task 6: Never speak thinking blocks

Reasoning models are usable in locat (`deepseek-r1:14b`, the `phonellm-alpha-1` GGUFs — their
`ollama show` lists a `thinking` capability and `<think>` among the stop tokens), but their
`<think>…</think>` text is currently synthesized and spoken aloud. It must never be **spoken**;
it may be **shown** in the UI. This is wrong in headphones mode today, which is why it comes
before the remaining frontend work.

Rejected alternative, do not relitigate without re-checking: disabling thinking server-side
(`think: false` is an Ollama `/api/chat` field, but `OLLamaLLMService` talks to
`/v1/chat/completions`) or per-family prompt hacks like Qwen's `/no_think`.

**Files:** create `thinking_filter.py`, `tests/test_thinking_filter.py`; modify `pipeline.py`,
`config.py`, `env.example`.

- [ ] **Step 1: confirm the frame types on the LLM→TTS path**
  The filter sits between the LLM service and the TTS service, so it must handle whatever text
  frame the LLM emits downstream. Confirm the exact class names in installed source, and check
  whether the assistant aggregator (downstream of TTS in locat's pipeline) then also excludes
  the suppressed text — reasoning must NOT accumulate in conversation history.
  Run: `grep -rn "class LLMTextFrame\|class TTSTextFrame\|class LLMFullResponseStartFrame\|class StartInterruptionFrame" .venv/lib/python3.12/site-packages/pipecat/frames/frames.py`
  Expected: the frame class names to match on, printed with line numbers.

- [ ] **Step 2: write the failing tests**
  `tests/test_thinking_filter.py`, mirroring `tests/test_rag_processor.py`'s `run_test` harness.
  Six cases: markers arriving whole in one frame; markers split across frames (`<th` + `ink>`);
  an unclosed block (no `</think>` ever arrives); no markers at all (byte-for-byte
  pass-through); an interruption mid-block resetting suppression; a `locat-thinking` RTVI
  server message carrying the suppressed text.
  Run: `uv run pytest tests/test_thinking_filter.py -v`
  Expected: FAIL — `ModuleNotFoundError: No module named 'thinking_filter'`.

- [ ] **Step 3: implement `thinking_filter.py`**
  A `FrameProcessor` that is stateful across streaming chunks: on `<think>` suppress downstream
  text until `</think>`; buffer enough to detect a marker split across frames and never emit a
  partial marker; reset suppression on interruption and at end of turn (and log when a block
  was left unclosed); pass everything through untouched when no markers appear. Emit the
  suppressed text as a `locat-thinking` server message and log it at DEBUG.
  Run: `uv run pytest tests/test_thinking_filter.py -v`
  Expected: PASS, all six.

- [ ] **Step 4: make the tags configurable**
  `LOCAT_THINKING_TAGS` (default `think,thinking`) — a `config.py` getter read via `_get()`,
  plus an `env.example` row, plus a test that a custom tag is honoured.
  Run: `uv run pytest tests/test_thinking_filter.py -v && grep -n "LOCAT_THINKING_TAGS" config.py env.example`
  Expected: tests pass; the var appears in both files.

- [ ] **Step 5: splice it into the pipeline**
  In `pipeline.py`, between the LLM and the TTS service, for both bots.
  Run: `uv run python -c "import bot, bot_moq, pipeline" && grep -n "ThinkingFilter\|thinking_filter" pipeline.py && uv run pytest -q`
  Expected: imports clean, the filter appears in `pipeline.py`, whole suite green.

- [ ] **Step 6: spoken verification — gated: HUMAN.md §2**
  Only a human with ears can confirm nothing is spoken. Leave this unchecked and make sure
  HUMAN.md §2 is accurate before moving on.

---

### Task 7: File upload button — gated: HUMAN.md §3

The repo owner asked for "a file upload button" but the design is undecided, and guessing it
would mean inventing an upload target, an indexing policy, and a size limit. **Do not implement
this task.** It stays gated until HUMAN.md §3 is answered and this task is rewritten with the
answers.

- [ ] **Step 1: wait for the spec — gated: HUMAN.md §3**
  When §3 is filled in, replace this task's steps with real ones derived from the answers, set
  its Status to `pending`, and give it a model.

---

### Task 8: voice-ui-kit variant alongside the hand-rolled client

The owner wants to try https://github.com/pipecat-ai/voice-ui-kit. This **reverses** the
original "vanilla TypeScript, no UI framework" decision — accepted explicitly, React included.
Tasks 4–5's hand-rolled client is NOT deleted here: it stays reachable at `/` until the human
picks a winner at HUMAN.md §4.

Verified facts (re-verify against the installed package): `@pipecat-ai/voice-ui-kit`,
BSD-2-Clause, **React-only**, Tailwind 4 + CSS variables; ships an all-in-one `ConsoleTemplate`
AND composable primitives (`PipecatAppBase`, `ConnectButton`, `ControlBar`, `VoiceVisualizer`,
`UserAudioControl`, `ErrorCard`, `FullScreenContainer`, `SpinLoader`). MoQ + this kit is
**already proven on this machine**:
`/Users/vipyned2/Documents/repos/vipyne/pipecat-moq-example/client/ui/src/index.tsx` is 79 lines
of `ConsoleTemplate` with `transportType="moq"` and `@pipecat-ai/moq-transport`, with
`startBotParams` pointing at `/start`. Port that wiring, not its UI choices.

**Files:** create `client/src/kit/` + a second Vite input; modify `client/vite.config.ts`,
`client/package.json`, and the server route that serves `client/dist`.

- [ ] **Step 1: preflight the toolchain**
  Run: `node --version && npm --version`
  Expected: both print versions. If either is missing, append the install instructions to
  HUMAN.md, mark this task `blocked: node missing`, and move on.

- [ ] **Step 2: install the kit into `client/`**
  Run: `cd client && npm install @pipecat-ai/voice-ui-kit react react-dom && ls node_modules/@pipecat-ai/voice-ui-kit/package.json`
  Expected: install succeeds; the package.json path exists.

- [ ] **Step 3: confirm the kit's real API surface before writing any JSX**
  In particular, how arbitrary RTVI **server messages** reach a component — the three custom
  panels depend on `locat-config`, `locat-rag` and `locat-thinking`. `PipecatAppBase`'s
  render-props expose the client; subscribe there.
  Run: `grep -rn "PipecatAppBase\|ConnectButton\|ControlBar\|VoiceVisualizer\|onServerMessage\|serverMessage" client/node_modules/@pipecat-ai/voice-ui-kit/dist/*.d.ts | head -40`
  Expected: the exported names and the server-message hook/event, quoted into a PROGRESS.md note.

- [ ] **Step 4: build the variant with primitives, NOT `ConsoleTemplate`**
  locat's whole reason for a custom frontend is that every part is inspectable; one opaque
  template throws that away. Keep locat's layout and keep all three panels as first-class locat
  code. Constraints that do not bend for a framework: `client/dist/` stays committed (running
  the bot needs **no Node**); **nothing may be fetched from a network at runtime** — Tailwind 4
  compiles at build time and fonts must be npm-vendored (`@fontsource-variable/*` self-hosts; a
  Google Fonts `<link>` would break the offline promise).
  Run: `cd client && npx tsc --noEmit && npm run build && ls dist/kit/index.html`
  Expected: no type errors, build succeeds, the second entry's HTML exists.

- [ ] **Step 5: serve it at `/kit` and prove no network fetches**
  Run: `grep -rn "kit" $(grep -rln "client/dist" --include=*.py . | head -1) && ! grep -rn "fonts.googleapis.com\|cdn\." client/dist/kit/*.html`
  Expected: the `/kit` route is registered in the server file; no CDN or Google Fonts
  references in the built HTML.

- [ ] **Step 6: commit the rebuilt bundle**
  Run: `git add -A && git commit -m "feat: voice-ui-kit variant at /kit" && test -z "$(git status --porcelain client/dist)"`
  Expected: committed; `client/dist` clean afterwards.

- [ ] **Step 7: side-by-side comparison — gated: HUMAN.md §4**
  Only the human can judge polish and whether the kit version still feels inspectable.

---

### Task 9: README rewrite — gated: HUMAN.md §4

Blocked until §4 records which UI won, because this task deletes the loser.

**Files:** `README.md`; delete the losing UI's source, deps and Vite entry.

- [ ] **Step 1: delete the losing frontend — gated: HUMAN.md §4**
  Read the choice recorded in HUMAN.md §4. Remove the loser's code, its `package.json` deps and
  its Vite entry, so the repo ships exactly one frontend. Rebuild `dist/`.
  Run: `cd client && npm run build && cd .. && uv run pytest -q`
  Expected: build clean, suite green.

- [ ] **Step 2: rewrite the README end to end**
  New quickstart (`./locat.sh configure` → `./locat.sh index-rag` → `./locat.sh start`);
  lifecycle section using `status`'s exact ownership wording, plus per-run session logs,
  `get-debug-log`, and per-command help; the RAG section (keep the existing "One place for all
  models: consolidate" section and the canary-trick subsection intact); the frontend section —
  if the kit won, say plainly that React/Tailwind are build-time only and the runtime still
  needs no Node; the WSL2 Windows story (browser handles audio; no headphones mode there).
  Remove every stale reference.
  Run: `grep -nE "start\.sh|stop\.sh|doctor\.sh|webrtc|locat\.sh models|configure\.sh -a" README.md`
  Expected: no output (exit 1 from grep) — none of those surfaces exist any more.

- [ ] **Step 3: prove the config table is complete**
  Run: `comm -23 <(grep -ohE "LOCAT_[A-Z_]+" config.py | sort -u) <(grep -ohE "LOCAT_[A-Z_]+" README.md | sort -u)`
  Expected: no output — every `LOCAT_*` var config.py reads appears in the README.

---

### Task 10: Offline checklist + zip-and-go notes

**Files:** `README.md`.

- [ ] **Step 1: "Verifying offline" section**
  Airplane-mode steps; note that the page is served from localhost so the browser UI works
  offline once the models are present.
  Run: `grep -n "Verifying offline" README.md`
  Expected: the heading exists.

- [ ] **Step 2: "Moving locat to another machine" section**
  Honest stretch-goal notes: same OS/arch → copying works, with the `models/` and `.venv`
  caveats; a different platform → re-run `uv sync --extra …` and `./configure.sh -i`; borrowed
  (symlinked) models are NOT included in a copy — cross-reference the consolidate section.
  Run: `grep -n "Moving locat to another machine" README.md && test $(grep -c '```' README.md) -eq $(( $(grep -c '```' README.md) / 2 * 2 ))`
  Expected: the heading exists and the code-fence count is even.

- [ ] **Step 3: whole suite green before handing over**
  Run: `uv run pytest -q && bash -n locat.sh && bash -n configure.sh`
  Expected: all tests pass; both scripts parse.

- [ ] **Step 4: final airplane-mode acceptance — gated: HUMAN.md §5**
  Passing this is what makes v2 done.


---

# Appendix: v2 HUMAN.md gate sections (archived 2026-09-16 with this plan)

## 2. Thinking blocks are never spoken (Task 6)
Needs your ears — no command can confirm what came out of the speakers.

- [ ] **Gate: a reasoning model speaks none of its reasoning.**
      ```bash
      ./configure.sh -i          # pick deepseek-r1:14b (or a phonellm GGUF)
      ./locat.sh start -t headphones
      ```
      Ask something that makes it think ("should I pay off my mortgage early or
      invest the difference?"). Confirm:
      1. Not one word of reasoning is spoken — the reply starts with its actual answer.
      2. `grep -E "thinking|locat-thinking" .locat/bot.log` shows the suppressed
         text and the RTVI message.
      3. Interrupt it mid-sentence, then ask again — the filter still behaves
         (nothing stuck suppressed, nothing leaked).
      Then `./locat.sh stop`.

## 3. File upload button — spec needed (Task 7)
The loop is forbidden from designing this. Answer these and it becomes real work.

- [ ] **Gate: decide what the upload button does.**
      1. Upload target — into `LOCAT_RAG_DATA_DIR` (default `./data`), or somewhere else?
      2. After upload, auto-run the equivalent of `./locat.sh index-rag`, or wait
         for you to trigger it?
      3. Which UI gets it — both, or only the winner of §4?
      4. Limits — max size, which extensions (`.txt/.md/.pdf/.csv` are what RAG reads)?
      5. Should the bot say something out loud when a file finishes indexing?
      Write the answers under this item, tick the box, and the next iteration will
      rewrite Task 7 from them.

## 4. Pick the frontend (Tasks 8 → 9)
Two UIs will be live: `/` (hand-rolled vanilla TS) and `/kit` (voice-ui-kit/React).

- [ ] **Gate: open both, pick one, and record the choice here.**
      ```bash
      ./locat.sh start          # then visit http://localhost:7860/ and /kit
      ```
      In each: connect, speak, interrupt, type a message, watch the
      what's-running and retrieved-context panels fill, mute/unmute, reconnect.
      Judge: polish; whether the three panels still read clearly; and whether the
      kit version still feels inspectable rather than magic.
      **Write your choice as a line under this item** — literally
      `CHOICE: kit` or `CHOICE: hand-rolled` — because Task 9 deletes the loser.
      Then `./locat.sh stop`.

## 5. Final acceptance — airplane mode (Task 10)
The whole point of the project. Needs you to toggle the network and hold a
conversation.

- [ ] **Gate: full offline conversation citing your own documents.**
      ```bash
      # put a few real documents in ./data first
      ./locat.sh index-rag
      ./locat.sh status          # confirm the rag line and the model paths
      # NOW turn off Wi-Fi / enable Airplane Mode
      ./locat.sh start
      ```
      Confirm: voice works both ways; typed input works; answers cite your
      documents by filename; the retrieved-context panel shows the chunks; no
      reasoning is spoken; nothing in `.locat/bot.log` reaches the network.
      Then `./locat.sh stop`. Passing this = locat v2 is done.
