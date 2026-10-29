# locat RAG Layer Implementation Plan

> **For agentic workers:** Executed by a ralph loop (`ralph/ralph.sh` +
> `ralph/PROMPT.md`), one task per iteration. Tick checkboxes in this file as you
> go; keep the Status table below current every iteration.

## Status
<!-- The loop refreshes this table and the Updated line EVERY iteration. -->
| # | Task | Status | Model |
|---|------|--------|-------|
| 1 | Setup: branch, sqlite-vec dep, wheels check, extension smoke | done | haiku |
| 2 | Data layer: `store.py` + `ingest.py` + tests, `locat.sh` switch | done | sonnet |
| 3 | Retrieval functions + tests (no LLM) | done | sonnet |
| 4 | Two-model wiring: rag_agent, tools, pipeline, prompts, panels | done | opus |
| 5 | Voice smoke test | done | — |
| 6 | Eval harness (`evals/`) | done | sonnet |
| 7 | Eval results review | gated: HUMAN.md §3 | — |

_Updated: 2026-10-01, iteration 22 (Task 5: human checked HUMAN.md §2 gate — voice smoke test passed, no failure notes; only Task 7's §3 gate remains)_

> **2026-10-12 — architecture superseded:** the two-model split these tasks built
> was collapsed to a single-model cascade (qwen2.5:14b + the four tools directly)
> for the conference workshop. Tasks below stay as history; current state is
> documented in README and ralph/PROGRESS.md.

**Goal:** locat answers real questions about the user's transaction CSV — lookups
("what did I pay Navy Federal last month"), filters ("my five biggest expenses in
March"), aggregations ("how much did I spend on groceries in 2025"), and semantic
questions over documents — all fully offline, via a **two-model split**:
**llama3.2:1b** is the pipeline LLM and does all the talking; it has one tool,
`ask_financial_records(question)`, whose handler runs a **qwen2.5:14b** records agent
(plain Python, outside the pipeline, Ollama native `/api/chat`, thinking off) that
holds the four typed retrieval tools (lookup / filter_rows / aggregate /
semantic_search) over ONE SQLite file (typed rows + FTS5 bm25 + sqlite-vec vec0).
The 8B composes a final 1–2 sentence spoken answer; the 1b repeats it verbatim
(`say_exactly`). The always-on numpy injection (`rag.py`/`rag_processor.py`) is
retired. Done looks like: `./locat.sh start`, the bot acknowledges in its own
voice ("Let me check your records") while the 8B works, speaks the four query
types plus the passphrase canary correctly, the panel shows which backend tools
ran with timings, no reasoning is ever spoken, and `evals/run_evals.py` reports
retrieval, delegation, relay-fidelity, and answer accuracy separately with a
latency breakdown.

**Tech stack:** Python 3.12 (pinned), `uv`; pipecat-ai 1.7.0
(`pipecat-ai[kokoro,moq,silero,whisper]`); Whisper-MLX STT / Ollama LLMs
(**llama3.2:1b** conversation / **qwen2.5:14b** records agent) /
Kokoro TTS / `nomic-embed-text` embeddings; SQLite (stdlib `sqlite3`) + FTS5 +
`sqlite-vec` (new dep); `pypdf`; `httpx` (async, backend agent); Vite +
vanilla TS in `client/`.

**Source plan/spec:** `/Users/vipyned2/.claude/plans/this-is-ultimately-going-fluttering-crystal.md`
(full design rationale; schema and tool signatures reproduced below). Previous
plan archived at `ralph/PLAN-v2.md` — its Task 6 (thinking filter) is now moot:
no thinking-capable model streams to TTS in this architecture.

## Global Constraints

- **Never push, never `gh`.** Commits stay local; the human reviews and pushes (HUMAN.md §1).
- **Never rewrite git history** — no `rebase`, `reset`, `clean`. (A rebase deleted this whole
  directory once; the deny list blocks these.)
- **`uv` for all Python** (`uv sync`, `uv run`, `uv add`) — never bare pip/venv. `npm` only
  inside `client/`.
- **Never run bare `uv sync`** — it uninstalls opt-in extras. Always name them:
  `uv sync --extra local-audio`.
- **Any change under `client/src/` rebuilds `client/dist/` in the same iteration**
  (`cd client && npm run build`) and commits it — `dist/` is committed and must never lag src.
- **Minimal comments** — fix the name instead; explanations belong in the README. Config files
  stay scannable.
- **One README per repo, top level only.** Other docs get non-README names (no README in `evals/`).
- **New env vars are `LOCAT_*`**, read via `config._get()`, documented in `env.example` and the
  README config table.
- **Full file paths in anything user-facing.**
- **Byte sizes:** never label a binary number with a decimal unit (`2^30` → `GiB`; `1e9` → `GB`).
- **Never delete anything under `data/`** — user documents. Never commit `.env`.
- **Anti-goals (from the design):** no fine-tuning; no vector-DB server; no network calls at
  inference (localhost Ollama HTTP is fine); never more than 5 chunks/rows in a tool result;
  no model ever authors SQL — tools take typed, whitelisted args and build parameterized
  queries server-side.
- **BOTH model defaults move in lockstep** across FIVE places: `config.py`
  (`DEFAULT_LLM_MODEL` front, `DEFAULT_RAG_LLM_MODEL` back), `locat.sh` (~line 32),
  `scripts/run_ollama.sh` (~line 39), `configure.sh` front-model literals, and
  `.env`/`env.example` — never change one without the others.

## Design reference (fixed decisions — do not re-litigate)

**SQLite schema** — one file at `$LOCAT_RAG_INDEX_DIR/locat.db`, `PRAGMA journal_mode=WAL`:

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- keys: schema_version, embed_model, embed_dim, serializer_version, chunk_tokens, chunk_overlap
CREATE TABLE files (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, indexed_at TEXT NOT NULL);
CREATE TABLE rows (
  id INTEGER PRIMARY KEY,              -- CSV Id (18 digits, fits int64)
  date TEXT NOT NULL, merchant TEXT NOT NULL, category TEXT NOT NULL,
  account TEXT NOT NULL, statement TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '', amount_cents INTEGER NOT NULL,  -- signed; negative = debit
  tags TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT '',
  reviewed TEXT NOT NULL DEFAULT '',
  source_path TEXT NOT NULL REFERENCES files(path));
CREATE INDEX idx_rows_date ON rows(date);
CREATE INDEX idx_rows_merchant ON rows(merchant COLLATE NOCASE);
CREATE INDEX idx_rows_category ON rows(category COLLATE NOCASE);
CREATE TABLE chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,   -- keys fts+vec rowids; never recycled
  source TEXT NOT NULL, page INTEGER,
  chunk_type TEXT NOT NULL CHECK (chunk_type IN ('doc','csv_row','rollup')),
  row_id INTEGER, text TEXT NOT NULL);
CREATE INDEX idx_chunks_source ON chunks(source);
CREATE VIRTUAL TABLE chunks_fts USING fts5(text);          -- standalone; rowid = chunks.id
CREATE VIRTUAL TABLE chunks_vec USING vec0(embedding float[{dim}]);  -- lazy, once dim known
```

`html.unescape` all text at ingest (data contains `&amp;`). Amounts stored as
integer cents. Incremental ingest: per-file sha256 vs `files` — unchanged → skip;
changed → transactional delete of that file's chunks/fts/vec/rows, re-embed;
file gone from disk → purge; `meta` mismatch (embed_model / serializer_version /
schema_version) → full rebuild with printed reason. Rollup chunks regenerate
whenever the CSV changes.

**Row serialization (SERIALIZER_VERSION = 1)** — one prose sentence-pair per row:
- Debit: `On September 8, 2026, spent $650.00 at Vanguard (Transfer) from account Adv Plus Banking (...9674). Statement: ACH HOLD VANGUARD BUY INVESTMENT ON 09/08. Transaction id 254373090987251397.`
- Credit: same with `received $X from {Merchant} ({Category}) into account {Account}`.
- Rollup (monthly × active category, Transfer excluded): `In March 2025, 14 transactions in Groceries totaled $412.33 spent; largest: $151.58 at Whole Foods Co-op on March 12.`

**Retrieval tools** (shared whitelists `COLUMNS = {date, merchant, category, account, amount}`,
`OPERATORS = {eq, neq, contains, lt, lte, gt, gte, between}`; names map through fixed
dicts to SQL fragments; results hard-capped at 5 items):
1. `lookup(query, limit≤5=3)` — all-digits ≥10 chars → PK lookup; else FTS5 with each
   token double-quoted (defuses FTS syntax), `bm25()` order, csv_row chunks joined to typed rows.
2. `filter_rows(column, operator, value, value2?, order_by∈{date,amount,amount_abs},
   descending=true, limit≤5=5)` — date `eq` with `YYYY-MM` prefix → `LIKE 'YYYY-MM%'`;
   text `eq` NOCASE; amounts parsed dollars→cents.
3. `aggregate(operation∈{count,sum,avg,min,max}, column="amount", filters[≤3],
   include_transfers=false)` — `sum` returns `{spent, received, net, count}`; result always
   carries `transfers_excluded`. (Contingency only if evals show arg fumbles: flat named params.)
4. `semantic_search(query, k≤5=4)` — embed via `/api/embed` → vec0 KNN top-20 + FTS top-20 →
   RRF (k=60) → `rerank()` no-op hook → top k over all chunk_types.
Category matching: validated string, not enum — exact NOCASE → `LIKE %v%` → zero matches
returns `{"rows": [], "hint": "closest: [...]"}` via `difflib.get_close_matches`.
These 4 schemas live ONLY in `rag_agent.TOOL_DEFS` (native `/api/chat` format) —
the single source of truth shared by `rag_agent`, evals, and tests.

**Two-model mechanics (verified against installed pipecat 1.7.0 + Ollama 0.33.2 —
trust these, re-verify only if versions change):**
- Streamed content deltas reach TTS immediately; tool calls run AFTER the stream
  ends (`base_llm.py:480-517`, `523-548`) — so the 1b's prompted acknowledgment
  ("Let me check your records.") is speaking while the handler runs. No canned
  `TTSSpeakFrame` filler.
- `params.result_callback(...)` triggers the follow-up completion by default;
  `FunctionCallResultProperties(run_llm=False)` suppresses it (the deterministic-
  relay contingency).
- Barge-in: functions default to `cancel_on_interruption=True`; the handler task
  is cancelled and `FunctionCallCancelFrame` cleans context (`llm_service.py:655`,
  `1626`). Consequence: the backend uses `httpx.AsyncClient` (cancel aborts the
  socket; Ollama stops on disconnect); NEVER catch `CancelledError`; no bare
  `except Exception` spanning awaits.
- pipecat applies NO function-call timeout by default — the backend enforces its
  own 35 s deadline.
- Native `/api/chat` accepts top-level `"think": false` and per-request
  `keep_alive`; thinking (when on) lands in `message.thinking`, never `content`.
- `llama3.2:1b` is pulled and tool-capable; `qwen3:8b` is pulled in Task 4.
- Ollama env: `OLLAMA_CONTEXT_LENGTH` is the per-model default `num_ctx`;
  `OLLAMA_MAX_LOADED_MODELS` must be pinned to 3 (1b + 8b + embed co-resident —
  eviction costs ~2–4 s weight swap per question).

### Task 1: Setup — branch, sqlite-vec, wheels, extension smoke

**Files:** `pyproject.toml`, `uv.lock` (via `uv add`).

- [x] **Step 1: Feature branch off main**
  `git switch -c vp-rag-tools` (if it already exists from a prior iteration, `git switch vp-rag-tools`).
  Run: `git branch --show-current`
  Expected: `vp-rag-tools`

- [x] **Step 2: Add sqlite-vec**
  `uv add sqlite-vec` then `uv sync --extra local-audio`.
  Run: `uv run python -c "import sqlite_vec; print(sqlite_vec.__version__ if hasattr(sqlite_vec,'__version__') else 'ok')"`
  Expected: prints without error.

- [x] **Step 3: Wheels tripwire (Intel-mac commitment)**
  Run: `uv run python scripts/check_wheels.py`
  Expected: exit 0, no missing-wheel report for `sqlite-vec` on macosx x86_64/arm64.
  If it fails, pin per the script's guidance in `[tool.uv]` — do NOT drop the dep silently.

- [x] **Step 4: Extension-loading smoke**
  Run: `uv run python -c "import sqlite3, sqlite_vec; c=sqlite3.connect(':memory:'); c.enable_load_extension(True); sqlite_vec.load(c); c.enable_load_extension(False); print(c.execute('select vec_version()').fetchone()[0])"`
  Expected: prints a vec version string (e.g. `v0.1.x`).

### Task 2: Data layer — store.py, ingest.py, tests, locat.sh switch

**Files:** create `store.py`, `ingest.py`, `tests/test_store.py`, `tests/test_ingest.py`,
`tests/fixtures/transactions-small.csv`; modify `config.py` (add `rag_db_path()`),
`locat.sh` (`index-rag`, status rag line), `tests/test_locat_sh.py`.
Old `rag.py`/`rag_processor.py` remain UNTOUCHED and green until Task 4.

- [x] **Step 1: Fixture CSV**
  `tests/fixtures/transactions-small.csv`: same 11-column header as
  `data/Transactions_2026-09-08T18-09-28.csv`, ~30 hand-picked rows covering: a
  `&amp;` HTML entity in Category (`Restaurants &amp; Bars`), several `Transfer`
  rows, credits and debits, at least two months and three merchants, one row with
  `Reviewed=Needs Review`, unique 18-digit Ids.
  Run: `uv run python -c "import csv; rows=list(csv.DictReader(open('tests/fixtures/transactions-small.csv'))); assert len(rows)>=25 and 'Amount' in rows[0], rows[0]; print(len(rows))"`
  Expected: row count ≥25.

- [x] **Step 2: Failing tests for store.py**
  `tests/test_store.py`: `ensure_schema` idempotent (call twice, same tables via
  `sqlite_master`); `has_index` False on missing/empty db, True after ingest;
  `stats_summary` exact shapes — `"index: N chunks from M files (DIR)"` populated,
  `"no index (run ./locat.sh index-rag)"` empty (match current `rag.py` strings
  exactly — check `rag.py` before writing); `connect` loads vec (`select vec_version()` works).
  Run: `uv run pytest tests/test_store.py -x -q`
  Expected: FAILS (module doesn't exist yet).

- [x] **Step 3: Failing tests for ingest.py**
  `tests/test_ingest.py`: port the chunking/extraction/FakeEmbedder invariants from
  `tests/test_rag.py` (keep them near-verbatim; they now import from `ingest`);
  new cases: serializer golden strings (debit + credit, HTML-unescaped, exact
  expected sentence); cents typing (`-650.00` → `-65000`); FakeEmbedder→vec0
  round-trip (meta records `embed_dim=32`; KNN returns the exact-text chunk first);
  incremental — ingest twice with a counting-embedder wrapper: second run embeds 0;
  touch (rewrite) one file: only its chunks re-embed; delete a file from the data
  dir: its rows/chunks purged; rollup chunks exist for fixture months, exclude
  Transfer; meta mismatch (change embed_model) → full rebuild.
  Run: `uv run pytest tests/test_ingest.py -x -q`
  Expected: FAILS.

- [x] **Step 4: Implement store.py + ingest.py**
  Per the Design reference above. `store.connect()` guards
  `hasattr(conn, "enable_load_extension")` and wraps `sqlite_vec.load` with an
  actionable `sys.exit` message. Move `extract`, `_extract_csv` (now returning typed
  dicts + serialized sentence), `chunk_text`, `OllamaEmbedder`, `FakeEmbedder`
  (byte-identical) from `rag.py` into `ingest.py` — copy, don't import (rag.py dies
  in Task 4). CLI: `uv run python ingest.py index` / `stats [--bare]`. Add
  `config.rag_db_path()` (env `LOCAT_RAG_DB`, default `$LOCAT_RAG_INDEX_DIR/locat.db`);
  document in `env.example`. Ingest prints a one-line hint that old
  `chunks.jsonl`/`embeddings.npy`/`manifest.json` are now inert (never delete them).
  Run: `uv run pytest tests/test_store.py tests/test_ingest.py -q`
  Expected: PASS.

- [x] **Step 5: Switch locat.sh + adapt its tests**
  `locat.sh`: `index-rag` → `exec uv run python ingest.py index`; status rag line →
  `uv run python ingest.py stats --bare`. Adapt `tests/test_locat_sh.py` rag cases:
  fixture dir builds a DB via `ingest.ingest(..., FakeEmbedder())`; the
  `index: \d+ chunks from N files` regex keeps passing.
  Pre-existing breakage found in iteration 6 (Task 2 Step 4): Task 2 Step 1
  (iteration 3) added `tests/fixtures/transactions-small.csv`, and `rag.py`'s
  old file-scanning tests glob the whole `tests/fixtures/` dir — they now see
  4 files instead of 3. Confirmed via `git stash` this fails identically before
  Step 4's changes, so it's not something store.py/ingest.py introduced.
  Currently failing: `tests/test_rag.py::test_index_writes_aligned_files_and_stats`
  (`assert stats.files == 3` sees 4) and
  `tests/test_locat_sh.py::test_status_rag_line_with_index` (regex expects
  "3 files"). Fix as part of this step — either give `rag.py`'s tests their own
  fixture subdir that excludes the CSV, or update both assertions to 4 files —
  before relying on "full suite PASS".
  Run: `uv run pytest -q`
  Expected: full suite PASS (old rag tests still green too).

- [x] **Step 6: Real-data ingest (needs Ollama + nomic-embed-text)**
  Start Ollama if needed (`nohup scripts/run_ollama.sh` per locat.sh conventions, or
  use a running one). Then twice: `uv run python ingest.py index`.
  Run: `uv run python ingest.py index && uv run python ingest.py index && uv run python -c "import sqlite3,config; print(sqlite3.connect(str(config.rag_db_path())).execute('select count(*) from rows').fetchone()[0])"`
  Expected: second index run reports 0 files re-embedded; row count prints 2673.
  Then `./locat.sh stop`.

### Task 3: Retrieval functions + tests (no LLM)

**Files:** create `retrieval.py`, `tests/test_retrieval.py`.

- [x] **Step 1: Failing tests**
  `tests/test_retrieval.py` against a fixture DB (built in a tmpdir via
  `ingest.ingest(..., FakeEmbedder())`, plus `tests/fixtures/note.txt`/`note.md` and
  a copy of `data/passphrase-smoke.txt` content as a doc file): `lookup` by 18-digit
  id, by merchant token, FTS-injection attempt (`'" OR 1'`, `'NEAR('`) returns
  normally, never raises; `filter_rows` every operator, `YYYY-MM` date prefix,
  NOCASE category, `order_by=amount_abs`; unknown category → empty rows + `hint`
  with closest matches; `aggregate` sum returns spent/received/net/count with
  Transfer excluded by default and included with `include_transfers=True`,
  `transfers_excluded` always present; `semantic_search` RRF returns the
  exact-text chunk first (FakeEmbedder trick: query must equal chunk text —
  normalize whitespace like the old tests), passphrase canary text retrievable,
  k>5 clamps to 5; every function returns an `elapsed_ms` (or timing tuple).
  Run: `uv run pytest tests/test_retrieval.py -x -q`
  Expected: FAILS.

- [x] **Step 2: Implement retrieval.py**
  Pure functions + `rrf()` + no-op `rerank()` hook, whitelist dicts, parameterized
  SQL only. Per Design reference.
  Run: `uv run pytest tests/test_retrieval.py -q`
  Expected: PASS.

- [x] **Step 3: Real-data cross-check**
  Run: `uv run python - <<'EOF'
import csv, config, retrieval
gold = round(sum(abs(float(r["Amount"])) for r in csv.DictReader(open(str(config.rag_data_dir()) + "/Transactions_2026-09-08T18-09-28.csv")) if r["Category"] == "Groceries" and r["Date"].startswith("2025") and float(r["Amount"]) < 0), 2)
agg = retrieval.aggregate(str(config.rag_db_path()), operation="sum", filters=[{"column":"category","operator":"eq","value":"Groceries"},{"column":"date","operator":"eq","value":"2025"}])
print(gold, agg)
assert abs(gold - agg["spent"]) < 0.01, (gold, agg)
EOF`
  (Adjust the call signature to whatever Step 2 implemented — the assertion is the point:
  SQL sum matches a raw-CSV python sum to the cent.)
  Expected: prints matching numbers, no assertion error.

- [x] **Step 4: Full suite**
  Run: `uv run pytest -q`
  Expected: PASS.

### Task 4: Two-model wiring — rag_agent, tools, pipeline, prompts, panels

**Files:** create `rag_agent.py`, `prompts/records_analyst.py`, `tests/test_rag_agent.py`,
`tests/test_tools.py`, `tests/test_pipeline_tools.py`; modify `tools.py` (new),
`pipeline.py`, `prompts/financial_advisor.py`, `bot.py`, `bot_moq.py`, `config.py`,
`locat.sh`, `scripts/run_ollama.sh`, `configure.sh`, `scripts/print_models.py`,
`.env`, `env.example`, `client/src/panels.ts` (+ rebuild `client/dist`), `README.md`;
delete `rag.py`, `rag_processor.py`, `tests/test_rag.py`, `tests/test_rag_processor.py`,
`tests/test_pipeline_rag.py`. NOTE: `services.py` needs NO change (front model is
plain llama3.2:1b — no `Settings.extra`, no think filter; TTS untouched).

- [x] **Step 1: Failing tests for rag_agent.py**
  `tests/test_rag_agent.py` against an injected fake `httpx.AsyncClient` (no
  network): `TOOL_DEFS` has exactly the 4 names in native format
  (`{"type":"function","function":{"name":...,"parameters":{...}}}`); a scripted
  tool-call round-trip (fake returns tool_calls → loop executes real `retrieval`
  against the fixture DB → tool message appended → fake returns compose) yields
  `AgentResult.answer` + populated `traces`/`timings`; malformed/unknown tool call
  gets ONE corrective tool-message round then fallback; round cap (max_rounds)
  forces a final no-tools compose request; connect-error → apologetic fallback
  with `error` set; deadline expiry → fallback; `asyncio.CancelledError` raised by
  the fake propagates out un-caught.
  Run: `uv run pytest tests/test_rag_agent.py -x -q`
  Expected: FAILS.

- [x] **Step 2: Implement rag_agent.py + prompts/records_analyst.py**
  `rag_agent.answer_question(question, *, db_path, embedder, model=None,
  api_url=None, client=None, max_rounds=4, deadline_secs=35.0) -> AgentResult`.
  Non-streaming native `/api/chat` rounds via `httpx.AsyncClient`; body includes
  `"think": False, "stream": False, "keep_alive": "30m",
  "options": {"temperature": 0.2}`; final round drops `tools`; retrieval calls via
  `asyncio.to_thread`; `strip_think()` one-line regex on the final content; error
  paths per Step 1; never catch `CancelledError`, no bare `except Exception`
  spanning awaits. Back prompt (`records_analyst.py` `SYSTEM_PROMPT`): answer ONLY
  from tool results; call as many tools as needed; if tools return nothing
  relevant, say you couldn't find it — never estimate or invent; final answer is
  1–2 short spoken sentences, plain words, no markdown, name the source (merchant
  and date, or file name), money and percentages written as words; transfers are
  excluded from spending totals unless the user asks about transfers.
  Run: `uv run pytest tests/test_rag_agent.py -q`
  Expected: PASS.

- [x] **Step 3: Failing tests for tools.py + pipeline**
  `tests/test_tools.py`: `build_tools(db_path, FakeEmbedder())` returns ONE
  `FunctionSchema` named `ask_financial_records` with a bundled handler; driving
  the handler with a hand-built `FunctionCallParams` (stub `llm` with recording
  `push_frame`, captured `result_callback`) against a stubbed
  `rag_agent.answer_question` asserts: result JSON has exactly the `say_exactly`
  key; an `RTVIServerMessageFrame` of type `locat-rag` was pushed carrying
  `query`, `answer`, `backend` (list of {tool,args,count,elapsed_ms}), `timings`,
  `chunks`. `tests/test_pipeline_tools.py` (replaces `test_pipeline_rag.py`):
  fixture DB present → `built.context` tools contain exactly
  `ask_financial_records`; no index → no tools, no crash.
  Run: `uv run pytest tests/test_tools.py tests/test_pipeline_tools.py -x -q`
  Expected: FAILS.

- [x] **Step 4: Implement tools.py; wire pipeline + prompts + preflights**
  `tools.py` handler: `await rag_agent.answer_question(...)` → RTVI push →
  `params.result_callback(json.dumps({"say_exactly": result.answer}))`; leave
  `cancel_on_interruption` at default True; optionally `timeout_secs=45` at
  registration as outer bound. `pipeline.py`: drop `rag`/`RAGProcessor` imports and
  stage; when `store.has_index()` build tools;
  `LLMContext(messages=[front SYSTEM_PROMPT], tools=...)`; extend `BuiltBot` with
  `llm`/`tts`; NO filler registration. `prompts/financial_advisor.py` rewrite
  (front): keep spoken-style, money-as-words, and not-a-licensed-advisor
  paragraphs; DELETE the "no access to accounts" paragraph; add — you have one
  tool, `ask_financial_records`: for ANY question about the user's transactions,
  spending, income, accounts, merchants, amounts, dates, totals, or saved
  documents you MUST call it, never answer from memory and never invent a number;
  when you call it, first say one short natural phrase like "Let me check your
  records."; the tool returns `say_exactly` — your entire reply after the tool
  must be that text word for word, nothing added, shortened, or rephrased;
  greetings and general questions are answered normally without the tool.
  `bot.py`: `_preflight_llm` called for BOTH `config.llm_model()` and
  `config.rag_llm_model()`; `_preflight_rag` → `store.preflight(...)`.
  `bot_moq.py`: stats line via `store.stats_summary`.
  Run: `uv run pytest tests/test_tools.py tests/test_pipeline_tools.py tests/test_locat_config.py -q`
  Expected: PASS (test_locat_config adapted in Step 5 if roles assert fails here — then rerun).

- [x] **Step 5: Two-model lockstep + Ollama env**
  `config.py`: `DEFAULT_LLM_MODEL = "llama3.2:1b"`, add
  `DEFAULT_RAG_LLM_MODEL = "qwen3:8b"` + `rag_llm_model()` getter
  (`LOCAT_RAG_LLM_MODEL`). `locat.sh` ~32: both defaults; `model_ready()` checks
  BOTH tags; start-wait message names whichever is missing.
  `scripts/run_ollama.sh` ~39: both defaults, pull loop over both tags, and
  export `OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-30m}"`,
  `OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-8192}"`,
  `OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-3}"` (1b + 8b + embed
  co-resident). `configure.sh`: move the "project default" catalog label to
  `llama3.2:1b`'s row (~line 128); fallback literals `qwen2.5:14b` →
  `llama3.2:1b` (~lines 943/945); one output sentence noting records analysis
  uses `LOCAT_RAG_LLM_MODEL` (qwen3:8b), configured separately — no redesign.
  `.env`: `LOCAT_LLM_MODEL=llama3.2:1b`, add `LOCAT_RAG_LLM_MODEL=qwen3:8b`.
  `env.example`: both + `LOCAT_RAG_DB`. `scripts/print_models.py`
  `model_entries()`: insert a `RAG-LLM` row after `LLM` (same ollama path logic);
  roles become `["STT","LLM","RAG-LLM","TTS","EMBED"]`; adapt
  `tests/test_locat_config.py` roles assert + `tests/test_print_models.py` +
  `tests/test_locat_sh.py` model expectations.
  Run: `grep -rn "qwen2.5:14b\|deepseek-r1" config.py locat.sh scripts/run_ollama.sh configure.sh .env env.example; uv run pytest tests/test_locat_sh.py tests/test_print_models.py tests/test_locat_config.py -q`
  Expected: grep finds nothing (or only catalog/docs mentions); tests PASS.

- [x] **Step 6: Frontend route lines**
  `client/src/panels.ts`: `LocatRag` gains optional `answer`, `backend`
  (array of {tool,args,count,elapsed_ms}), `timings`; render one route line per
  backend tool call (e.g. `aggregate {"operation":"sum",...} · 5 rows · 4ms`)
  above the chunks; chunk rendering unchanged.
  Run: `cd client && npm run build && cd ..`
  Expected: clean build; `client/dist` updated (commit it this iteration).

- [x] **Step 7: Delete the old RAG stack**
  Delete `rag.py`, `rag_processor.py`, `tests/test_rag.py`,
  `tests/test_rag_processor.py`, `tests/test_pipeline_rag.py` (their still-relevant
  cases were ported in Tasks 2–3).
  Run: `grep -rn "import rag\b\|from rag import\|rag_processor" --include="*.py" . | grep -v ".venv" ; uv run pytest -q`
  Expected: grep empty (rag_agent is fine — match exactly); full suite PASS.

- [x] **Step 8: qwen3 pull + native think probe**
  `ollama pull qwen3:8b` (large download, sanctioned). If that errors with a
  manifest/"not found" style message (the human hit this 2026-09-23 — see
  HUMAN.md §2 — Ollama's registry did not have the `qwen3:8b` tag available),
  fall back to pulling the GGUF straight from Hugging Face, which `ollama pull`
  supports natively: `ollama pull hf.co/Qwen/Qwen3-8B-GGUF:Q4_K_M`, then
  reference that full `hf.co/...` string as `LOCAT_RAG_LLM_MODEL` everywhere
  Step 5's lockstep touches (`config.py` default, `locat.sh`, `run_ollama.sh`,
  `.env`/`env.example`) instead of the bare `qwen3:8b` tag — note the actual
  tag used in PROGRESS.md so later steps and evals reference the same string.
  Run: `curl -s localhost:11434/api/chat -H 'Content-Type: application/json' -d '{"model":"qwen3:8b","messages":[{"role":"user","content":"hi"}],"think":false,"stream":false}' | head -c 600`
  (swap in the `hf.co/...` tag if that's what was actually pulled.)
  **Result (iteration 18): the bare `qwen3:8b` tag pulled from the Ollama
  registry fine — the HF fallback was NOT needed and no lockstep string
  changed.** The human's "not in catalog" note was about `configure.sh`'s
  hardcoded menu, not the registry. Probe returned no `thinking` field and no
  `<think>` in content; a `think:true` control DID populate `thinking`, so the
  flag is genuinely doing the work. No `/no_think` fallback needed.
  Expected: normal completion, NO `thinking` field, no `<think>` in content. If
  either appears, append qwen3's `/no_think` soft switch to the back system
  prompt as fallback and note it in PROGRESS.md (`strip_think` catches stragglers
  either way).

- [x] **Step 9: README retrieval section**
  Rewrite the "RAG (documents)" section: the two-model architecture (1b converses
  and relays, qwen3:8b records agent with four tools), one SQLite file, the
  serialization idea, index/status commands unchanged (`./locat.sh index-rag`),
  canary trick preserved, `LOCAT_RAG_LLM_MODEL` and `LOCAT_RAG_DB` in the config
  table, note the old index files are inert.
  Run: `grep -n "ask_financial_records\|LOCAT_RAG_LLM_MODEL\|sqlite" README.md | head`
  Expected: hits in the retrieval section.

### Task 5: Voice smoke test — gated: HUMAN.md §2

- [x] **Gate: ears-on end-to-end check** — see `ralph/HUMAN.md` §2. (Human ticked the §2 gate box, no failure notes — cleared 2026-10-01, iteration 22.)

### Task 6: Eval harness

**Files:** create `evals/queries.jsonl`, `evals/run_evals.py`, `tests/test_evals.py`.

- [x] **Step 1: Gold queries**
  `evals/queries.jsonl`: ~20 retrieval entries
  `{id, query, type, expected_tool, expected_args_subset, expected_source_ids, expected_answer}` —
  5 lookup / 5 filter / 5 aggregate / 5 semantic, including the passphrase canary
  and one "not in the data" refusal probe — PLUS ~4 chit-chat probes
  `{id, query, "type": "chitchat", "expect_delegation": false}` ("how are you
  today", "what does diversification mean", "should I panic when markets drop",
  "tell me something encouraging"). Compute every `expected_answer` and
  `expected_source_ids` from the REAL CSV with a throwaway script (do not guess);
  spot-check two by hand against `data/Transactions_2026-09-08T18-09-28.csv`.
  Run: `uv run python -c "import json; qs=[json.loads(l) for l in open('evals/queries.jsonl')]; assert len(qs)>=22; from collections import Counter; print(Counter(q['type'] for q in qs))"`
  Expected: ~5 per retrieval type + ~4 chitchat.

- [x] **Step 2: Harness + offline unit test**
  `evals/run_evals.py` stages (all localhost):
  `--stage retrieval` — gold args → `retrieval.py`; source-id hit rate (no LLM).
  `--stage toolcall` — TWO sub-reports: (a) FRONT delegation: front system prompt +
  the single `ask_financial_records` OpenAI-format schema → `/v1/chat/completions`
  (streamed httpx, matching the production pipecat path) with llama3.2:1b —
  delegation-recall (data questions that produced the tool call),
  delegation-precision (chit-chat probes that did NOT), ack-rate (non-empty
  content deltas before the first tool_call delta), question-arg sanity (arg
  contains key query tokens), 1b tool-call TTFT; (b) BACK selection:
  `rag_agent.TOOL_DEFS` + back prompt + query → native `/api/chat` non-streaming
  with qwen3:8b — correct-tool %, args-subset %.
  `--stage e2e` — run `rag_agent.answer_question()` for real (answer containment
  with normalized numbers, per-round timings), then a RELAY leg: feed
  `{"say_exactly": answer}` as the tool result into a second 1b `/v1` completion;
  relay fidelity = normalized exact match (lowercase, collapse whitespace, strip
  punctuation) + `difflib.SequenceMatcher` ratio, flag <0.95 as relay failure.
  Latency columns: 1b TTFT / ack-to-tool-call gap / backend total (round
  breakdown) / relay TTFT.
  `--stage isolate` — each e2e failure re-run with gold rows pasted into the BACK
  prompt: pass ⇒ retrieval failure, fail ⇒ model failure; relay failures are
  attributed to the 1b, never to retrieval. Never conflate.
  Report: per-type retrieval accuracy, delegation recall/precision, ack-rate,
  back tool accuracy, answer accuracy, relay fidelity, latency columns; note STT
  is measured from live `bot.log`, out of harness scope.
  `tests/test_evals.py`: scoring, arg-subset matching, number normalization, and
  relay-fidelity scorer — fully offline.
  Run: `uv run pytest tests/test_evals.py -q`
  Expected: PASS.

- [x] **Step 3: Full run**
  Ollama up with llama3.2:1b + qwen3:8b + nomic-embed-text; real DB indexed.
  Run: `uv run python evals/run_evals.py 2>&1 | tail -40`
  Expected: completes all four stages, prints the report table. Paste the table
  into PROGRESS.md. Then `./locat.sh stop`.

- [x] **Step 4: Suite still green**
  Run: `uv run pytest -q`
  Expected: PASS.

### Task 7: Eval results review — gated: HUMAN.md §3

- [ ] **Gate: human reads the eval report and signs off** — see `ralph/HUMAN.md` §3. gated: HUMAN.md §3
