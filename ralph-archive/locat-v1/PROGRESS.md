# PROGRESS.md — ralph loop notes

Append-only. Newest at the bottom. Each iteration adds a dated entry under
`## Iterations`.

## Versions
(tool/library versions as they are installed or verified; pinned versions the
loop must not "upgrade")

- Python **3.12** — pinned in `.python-version`; 3.14 is too new for the ML wheels.
- **pipecat-ai 1.7.0** — `pipecat-ai[kokoro,moq,silero,whisper]`. The
  `pipecat-context-hub` MCP index LAGS this version: get concepts from the hub,
  verify exact names against `.venv/lib/python3.12/site-packages/pipecat/`, and
  prefer non-deprecated symbols. `PipelineWorker`/`WorkerRunner` are current;
  `PipelineTask`/`PipelineRunner` are deprecated.
- **`moq_rs` 0.3.3** — has NO `producer.used()`, unlike the pattern in
  pipecat-moq-example. The audio-subscriber gate is track-open + client-ready based.
- `fastapi` + `uvicorn` are DIRECT dependencies (they arrived when
  `pipecat-ai-prebuilt` was dropped in v2 Task 4).
- `pypdf` for PDF extraction — pure Python, chosen deliberately over pymupdf to
  avoid wheel gaps. Table-heavy PDFs extract poorly; that is a known trade.
- `[tool.uv]` pins onnxruntime / numba / llvmlite / cryptography back to their last
  Intel-mac releases. If `uv sync` ever builds from source, run
  `python3 scripts/check_wheels.py` to find which platform lost a wheel — do NOT
  unpin to make a build pass.
- Node + npm are required only for `client/` work; the runtime needs neither
  because `client/dist/` is committed.
- (add `sqlite-vec` and `qwen3:8b` entries here as Tasks 1 and 4 install them)

## Blockers
(none yet — exact command + verbatim error when a step fails twice in one
iteration; a blocker seen three iterations in a row is not retried)

## Iterations

- **2026-09-16 — iteration 0 (human, not a loop run).** Re-pointed the loop at the
  LO-FI RAG plan: SQLite (rows + FTS5 + sqlite-vec) behind four typed retrieval
  tools (lookup / filter_rows / aggregate / semantic_search), qwen3:8b with
  thinking disabled, always-on `rag_processor` retired. Previous locat-v2 plan
  archived to `ralph/PLAN-v2.md` (its HUMAN gates appended there); v2 Task 6
  "never speak thinking blocks" is subsumed by Task 4's think filter. Promise is
  now `LOFI_RAG_COMPLETE`. Design source:
  `/Users/vipyned2/.claude/plans/this-is-ultimately-going-fluttering-crystal.md`.

- **2026-09-21 — iteration 0 again (human, not a loop run; no loop iterations had
  run).** Architecture revision before any code: TWO models. llama3.2:1b is the
  pipeline LLM (all conversation) with a single tool `ask_financial_records`;
  its handler runs a qwen3:8b records agent OUTSIDE the pipeline (`rag_agent.py`,
  native `/api/chat` via async httpx, `think:false`, ≤4 tool rounds, 35 s
  deadline) holding the four retrieval tools; the 8B's composed answer returns as
  `{"say_exactly": ...}` and the 1b repeats it verbatim. No canned filler — the
  1b is prompted to acknowledge naturally before the tool call (verified: pipecat
  streams pre-tool-call text to TTS). `think_filter.py` dropped from the plan (no
  thinking model streams to TTS anymore). Tasks 1–3 unchanged; Task 4/6 and
  HUMAN.md §2/§3 rewritten; new env var `LOCAT_RAG_LLM_MODEL=qwen3:8b`, front
  default `LOCAT_LLM_MODEL=llama3.2:1b`; `OLLAMA_MAX_LOADED_MODELS=3` pinned
  (1b + 8b + embed co-resident). Verified against pipecat 1.7.0 source: ack
  streaming (base_llm.py:480-517), cancel-on-interruption (llm_service.py:655,
  1626 — backend must use AsyncClient, never catch CancelledError),
  FunctionCallResultProperties(run_llm=False) available as deterministic-relay
  contingency; Ollama 0.33.2 native API accepts top-level think:false and
  per-request keep_alive. Design source updated in place (same plan file).

- **2026-09-23 — iteration 1 (Task 1: setup).** Created feature branch `vp-rag-tools`;
  added `sqlite-vec==0.1.9` via `uv add`; wheels check passed on all platforms
  (intel-mac, arm-mac, linux-x86, windows); extension smoke test: vec_version()
  returned `v0.1.9`. Next: Task 2 (data layer).

- **2026-09-23 — iteration 2 (HUMAN.md bug report + Task 1 checkbox fix).**
  `ralph/HUMAN.md` §2 had a dated 2026-09-23 note: `qwen3:8b` is not listed in
  catalog, is a Hugging Face URL needed? Per the loop rule, turned it into plan
  work rather than picking a new task: amended Task 4 Step 8 in `ralph/PLAN.md`
  with a fallback — if `ollama pull qwen3:8b` errors (tag not in the registry),
  pull the GGUF directly from Hugging Face (`ollama pull
  hf.co/Qwen/Qwen3-8B-GGUF:Q4_K_M`, which `ollama pull` supports natively) and
  use that full `hf.co/...` string as `LOCAT_RAG_LLM_MODEL` everywhere Step 5's
  lockstep touches, instead of the bare `qwen3:8b` tag. HUMAN.md §2 gate stays
  unchecked (Task 5 not reached yet); the note stays as the record of why Step 8
  has the fallback.
  Also found iteration 1 had run all four Task 1 steps successfully but left
  their checkboxes unticked in `ralph/PLAN.md` even though the Status table
  said "done". Re-ran all four verify commands this iteration — all still pass
  (`sqlite_vec` 0.1.9 imports; `check_wheels.py` exit 0 on all 4 platforms;
  `vec_version()` → `v0.1.9`; branch is `vp-rag-tools`) — and ticked the boxes.
  Next: Task 2 (data layer: `store.py` + `ingest.py`).

- **2026-09-23 — iteration 3 (Task 2 Step 1: fixture CSV).** Created
  `tests/fixtures/transactions-small.csv`: 30 rows, same 11-column header as
  the real CSV (`Date,Merchant,Category,Account,Original Statement,Notes,
  Amount,Tags,Owner,Reviewed,Id`). Covers: `Restaurants &amp; Bars` (HTML
  entity, 4 rows), 4 Transfer rows (Vanguard x2, City Transfer Savings x2), 3
  credits (payroll x2, a refund) among mostly debits, two months (2025-02,
  2025-03), 10 distinct merchants, one `Reviewed=Needs Review` row, unique
  18-digit sequential Ids (`100000000000000001`-`...030`). Verify command from
  the plan passed (30 rows, `Amount` key present); also ran an extra ad-hoc
  script checking each individual requirement (uniqueness/length of ids,
  entity present, transfer count, credit+debit present, month count, merchant
  count, exactly one Needs Review row) — all passed. Next: Task 2 Step 2
  (failing tests for `store.py`).

- **2026-09-23 — iteration 4 (Task 2 Step 2: failing tests for store.py).**
  Wrote `tests/test_store.py` against the not-yet-written `store` module per
  the Design reference schema (`meta`, `files`, `rows`, `chunks`, `chunks_fts`
  — `chunks_vec` is intentionally excluded from the "expected tables" check
  since the plan says it's created lazily once embed dim is known, not by
  `ensure_schema`). Covers: `ensure_schema` creates the 5 core tables and is
  idempotent (same `sqlite_master` names after calling twice); `connect` loads
  the `sqlite-vec` extension (`select vec_version()` returns a string);
  `has_index` False on a missing db file, False on an empty-but-schema'd db,
  True once a `files`+`chunks` row exists (inserted directly via raw SQL
  through the connection `store.connect()` returns — `ingest.py` doesn't
  exist yet, so tests exercise `store`'s own tables, not a real ingest);
  `stats_summary` returns the exact `rag.py`-matching strings: `"no index (run
  ./locat.sh index-rag)"` for missing/empty, `"index: N chunks from M files
  (DIR)"` for populated (verified by reading `rag.py`'s `stats_summary`
  before writing, per the plan's instruction).
  Ran `uv run pytest tests/test_store.py -x -q` — FAILS as expected:
  `ModuleNotFoundError: No module named 'store'`. This confirms the test step
  per TDD; `store.py` itself is NOT written yet — that's Task 2 Step 4, after
  Step 3 (failing tests for `ingest.py`) is also written first.
  Next: Task 2 Step 3 (failing tests for `ingest.py`).

- **2026-09-23 — iteration 5 (Task 2 Step 3: failing tests for ingest.py).**
  Wrote `tests/test_ingest.py` against the not-yet-written `ingest` module.
  Ported near-verbatim (import from `ingest` instead of `rag`): `chunk_text`
  budget/overlap/coverage/hard-split/sentence-boundary tests; `extract()` for
  txt/md/pdf/unknown-extension (doc types only — CSV extraction changed shape,
  see below); `FakeEmbedder`/`OllamaEmbedder` shape/determinism/HTTP-error
  tests. Dropped the old file-based `index()`/`retrieve()`/`has_index()` tests
  and the old blob-style CSV extraction tests (`"header: value"` sentences)
  since `_extract_csv` now returns typed row dicts per the Design reference,
  not one blob-per-file — those don't port forward.
  New cases per the plan, which fix the following `ingest.py` API contract for
  Step 4 to implement: `parse_amount_cents(str) -> int` (cents, e.g.
  `"-650.00"` → `-65000`); `serialize_row(dict) -> str` (debit/credit prose,
  golden-tested against the Design reference's own Vanguard example verbatim,
  plus a credit example I authored); `extract(path)` for `.csv` delegates to
  `_extract_csv` which returns typed dicts (`id` int, `amount_cents` int,
  html-unescaped merchant/category/account/statement/tags/owner, plus a
  `sentence` key holding `serialize_row`'s output) — asserted against the real
  30-row fixture (unique ids, `Restaurants &amp; Bars` unescaped to `Restaurants
  & Bars` in both the field and the sentence); `ingest(data_dir, db_path,
  embedder, chunk_tokens=, overlap=) -> IngestStats` (fields: `files`, `chunks`,
  `rows`, `embedded`, `embed_model`, `rebuilt`, `reason`) driving `store.connect`
  + `store.ensure_schema` — asserted end-to-end against a tmp SQLite db: 30
  `rows` + 30 `chunks(chunk_type='csv_row')`; rollup chunks exist and never
  mention Transfer; `meta.embed_dim` recorded; `chunks_vec` KNN round-trip
  (`sqlite_vec.serialize_float32`) returns the exact source chunk's `chunks.id`
  as top rowid; incremental re-ingest with a counting-embedder wrapper embeds 0
  texts when nothing changed, embeds only a touched file's own chunk count
  when one file is rewritten (CSV rows untouched), purges `chunks`+`files` rows
  for a file deleted from disk; an embed-model change in `meta` triggers a full
  rebuild (`stats.rebuilt is True`, `meta.embed_model` updated, row count not
  doubled).
  Ran `uv run pytest tests/test_ingest.py -x -q` — FAILS as expected:
  `ModuleNotFoundError: No module named 'store'` at the `import store` line
  (both `store` and `ingest` are unwritten; Step 4 implements both together per
  the plan). Note for Step 4: `chunks.id` is the shared FTS/vec rowid (per the
  `chunks` table's own comment) — `chunks_vec` inserts must use
  `INSERT INTO chunks_vec(rowid, embedding) VALUES (?, ?)` with that same id, and
  `chunks_vec` itself is created lazily on first embed batch once `dim` is known
  (excluded from `store.ensure_schema`'s table set per Step 2's tests).
  Next: Task 2 Step 4 (implement `store.py` + `ingest.py`).

- **2026-09-23 — iteration 6 (Task 2 Step 4: implement store.py + ingest.py).**
  `store.py`: `connect()` opens WAL mode, guards `hasattr(conn,
  "enable_load_extension")` with an actionable `sys.exit`, loads `sqlite_vec`,
  calls `ensure_schema()` (creates `meta`/`files`/`rows`/`chunks`/`chunks_fts`
  via one `executescript`, `CREATE ... IF NOT EXISTS` throughout — idempotent).
  `chunks_vec` is NOT created here (dim unknown until first embed batch).
  `has_index()`/`stats_summary()` open their own short-lived plain
  `sqlite3.connect()` (no vec load, no schema creation) so a missing/empty db
  reports correctly without ever creating a file.
  `ingest.py`: ported `PageText`, `chunk_text`, `OllamaEmbedder`,
  `FakeEmbedder` byte-identical from `rag.py`; `extract()` still returns
  `list[PageText]` for `.txt/.md/.pdf` but now returns `list[dict]` (typed row
  + `sentence`) for `.csv` via a rewritten `_extract_csv` (was blob-per-file,
  now one typed dict per row, `html.unescape`d). New: `parse_amount_cents`
  (`Decimal` round-half-up to cents), `serialize_row` (golden-string exact
  match — `_format_date` avoids `%-d`/`%#d` platform-specific strftime flags
  by building `"{month} {day}, {year}"` manually; `_format_money` does
  integer divmod instead of float division to dodge float-precision
  drift on large amounts). `ingest()`: reads `meta.embed_model` /
  `serializer_version` / `schema_version` before touching anything; a
  mismatch (or embedder.model differing) triggers `_full_wipe` (clears
  `chunks_fts`, drops `chunks_vec` if present, clears `chunks`/`rows`/`files`)
  and sets `rebuilt=True` + a human-readable `reason`; then per-disk-file
  sha256 diffing against `files` — purges anything gone from disk, re-ingests
  anything new/changed (CSV rows replace via `store` `rows` table + one
  `csv_row` chunk per row; docs chunk via `chunk_text`), and only when the CSV
  itself changed does it regenerate `rollup` chunks (monthly × category,
  `WHERE category != 'Transfer' AND amount_cents < 0`, SQL `GROUP BY` — text
  matches the design's rollup sentence shape). `chunks.id` (AUTOINCREMENT) is
  reused as the `chunks_fts`/`chunks_vec` rowid via `cursor.lastrowid`,
  confirmed by the vec-roundtrip test. CLI (`ingest.py index` / `stats
  [--bare]`) added per the plan though not exercised by pytest — `index`
  preflights the embed model, prints a hint if old `chunks.jsonl`/
  `embeddings.npy`/`manifest.json` exist (never deletes them); `stats` prints
  `store.stats_summary()` plus `meta` details unless `--bare`. Added
  `config.rag_db_path()` (`LOCAT_RAG_DB`, default `$LOCAT_RAG_INDEX_DIR/locat.db`) —
  env.example doc line for it is still TODO (belongs with Step 5's env-var
  pass, not called out separately in Step 4's file list).
  Ran `uv run pytest tests/test_store.py tests/test_ingest.py -q` — 41 passed
  (9 store + 32 ingest). Ticked Step 4.
  Then ran the full suite (`uv run pytest -q`) to sanity-check: 2 pre-existing
  failures, confirmed via `git stash` to fail identically on the prior commit
  (before this iteration's changes) — `tests/test_rag.py::
  test_index_writes_aligned_files_and_stats` (`assert stats.files == 3` sees 4)
  and `tests/test_locat_sh.py::test_status_rag_line_with_index` (regex expects
  "3 files"). Root cause: Task 2 Step 1 (iteration 3) added
  `tests/fixtures/transactions-small.csv` into the SAME `tests/fixtures/` dir
  that `rag.py`'s old file-scanning tests glob wholesale, so they now count 4
  files instead of 3. Not something store.py/ingest.py caused. Amended Task 2
  Step 5 in `ralph/PLAN.md` to fix this (give rag.py's tests their own fixture
  subdir, or bump the expected count) before its "full suite PASS" line is
  trusted.
  Next: Task 2 Step 5 (switch locat.sh + adapt its tests + fix the two
  pre-existing failures noted above).

- **2026-09-23 — iteration 7 (Task 2 Step 5: switch locat.sh + adapt tests).**
  `locat.sh`: `rag_line()` now calls `uv run python ingest.py stats --bare`
  (was `rag.py stats --bare`); `index-rag)` case now execs `uv run python
  ingest.py index` (was `rag.py index`). Both `ingest.py`'s `_cli_index`/
  `_cli_stats` already existed from Step 4 and matched the expected CLI
  surface (`index`, `stats [--bare]`) with no changes needed there.
  Fixed the two pre-existing failures noted in iteration 6 by choosing the
  "update assertions to 4 files" option (simpler than a new fixture subdir,
  and `rag.py`/`tests/test_rag.py` are deleted wholesale in Task 4 Step 7
  anyway, so it's short-lived): `tests/test_rag.py::
  test_index_writes_aligned_files_and_stats` now asserts `stats.files == 4`
  and `len(manifest["files"]) == 4`. `tests/test_locat_sh.py::
  test_status_rag_line_with_index` rewritten to build the DB via
  `ingest.ingest(REPO/"tests"/"fixtures", index_dir/"locat.db",
  ingest.FakeEmbedder())` (was `rag.index(...)`) and asserts "4 files" in the
  status line (`config.rag_db_path()` defaults to `LOCAT_RAG_INDEX_DIR/
  locat.db`, so passing `LOCAT_RAG_INDEX_DIR` still routes correctly with no
  new env var needed in the test).
  Also added the `LOCAT_RAG_DB` line to `env.example` (was flagged as TODO
  in iteration 6 — belongs with the global constraint that every `LOCAT_*`
  var gets documented there; `config.rag_db_path()`'s getter already existed
  from Step 4). README's config table still needs a `LOCAT_RAG_DB` row —
  left for Task 4 Step 9's README rewrite since that step already owns the
  full retrieval-section README pass.
  Ran `uv run pytest -q` — 144 passed, 0 failures (up from 142 passed + 2
  pre-existing failures in iteration 6).
  Next: Task 2 Step 6 (real-data ingest — needs Ollama + nomic-embed-text
  running; gated on nothing but hasn't been attempted yet).

- **2026-09-23 — iteration 8 (Task 2 Step 6: real-data ingest; Task 2 done).**
  Checked HUMAN.md first: the dated qwen3:8b note under §2 was already turned
  into plan work in iteration 2 (Task 4 Step 8's Hugging Face fallback) — gate
  stays unchecked until Task 5 is reached, nothing new to do there this
  iteration.
  Ollama was already running (not started by locat) with `nomic-embed-text:
  latest`, `llama3.2:1b`, and others pulled — no `qwen3:8b` yet (expected;
  that's Task 4 Step 8). `uv run python ingest.py index` against the real
  `data/` dir (`Transactions_2026-09-08T18-09-28.csv` + `passphrase-smoke.txt`,
  `.DS_Store` correctly skipped as unsupported extension): "indexed 3246
  chunks from 3 files, 2673 rows with nomic-embed-text". Ran it again:
  identical summary line (rag_db_path is `models/rag-index/locat.db`, which is
  gitignored, so a second CLI run can't show its own diff from the stdout
  alone) — row count query confirmed 2673 both times. Since the CLI's print
  line doesn't surface `IngestStats.embedded`, ran `ingest.ingest(...)`
  directly a third time via a one-off script to check the field the plan's
  verify command doesn't expose: `embedded=0, files=3, chunks=3246, rows=2673,
  rebuilt=False` — confirms the incremental-skip path actually took, not just
  that the printed totals matched by coincidence.
  `./locat.sh stop` afterward: "ollama: running, NOT started by locat —
  leaving it alone" (correct — did not stop the human's own Ollama process).
  `git status` clean — the db file is gitignored (`models/`), so this step
  touches no tracked files; only `ralph/PLAN.md`/`ralph/PROGRESS.md` change
  this iteration.
  Task 2 (data layer) is now fully done — Status table updated.
  Next: Task 3 Step 1 (failing tests for `retrieval.py`).

- **2026-09-23 — iteration 9 (Task 3 Step 1: failing tests for retrieval.py).**
  Checked HUMAN.md first: the dated qwen3:8b note under §2 was already turned
  into plan work in iteration 2; no new dated note since. Nothing to re-gate.
  Wrote `tests/test_retrieval.py` against the not-yet-written `retrieval`
  module, using a `db` pytest fixture that builds a fixture DB in `tmp_path`
  via `ingest.ingest(..., FakeEmbedder())` over
  `tests/fixtures/transactions-small.csv` + `tests/fixtures/note.txt` +
  `tests/fixtures/note.md` + a copy of `data/passphrase-smoke.txt` (the
  canary doc). This fixes the following API contract for Step 2 to implement:
  `lookup(db_path, query, limit=3) -> {"rows": [...], "elapsed_ms": ...}`
  (all-digit ≥10-char query -> PK lookup by `rows.id`; else FTS5 over
  `csv_row` chunks joined back to `rows`, results capped at 5);
  `filter_rows(db_path, column, operator, value, value2=None,
  order_by="date", descending=True, limit=5) -> {"rows": [...],
  "elapsed_ms": ...}` (or `{"rows": [], "hint": "...", "elapsed_ms": ...}` for
  an unmatched category), each returned row a dict with plain-dollar `amount`
  (not cents); `aggregate(db_path, operation, column="amount", filters=None,
  include_transfers=False) -> dict` — `sum`+`amount` returns
  `{"spent","received","net","count"}`, other operation/column combos return
  `{"result","count"}`, both always carry `transfers_excluded` +
  `elapsed_ms`; `semantic_search(db_path, query, embedder, k=4) ->
  {"results": [...], "elapsed_ms": ...}` via vec0 KNN top-20 + FTS top-20 RRF
  fusion, `k` clamped to 5.
  Test cases: lookup by 18-digit id and by merchant token; limit clamps to 5;
  4 FTS-injection-shaped queries (`'" OR 1'`, `NEAR(`, `'"""'`, `OR 1=1`) never
  raise; `filter_rows` for all 7 operators (eq/neq/contains/lt/lte/gt/gte/
  between), each cross-checked against a plain-Python filter over the same
  fixture CSV rather than hand-computed numbers (fixture ids are assigned in
  chronological order, so ascending-date SQL order and ascending-id gold
  order coincide — verified this holds before relying on it); `YYYY-MM` date
  prefix; NOCASE category eq; `order_by=amount_abs`; unknown category ->
  `hint` naming the real category via difflib; `aggregate` sum cross-checked
  against a Python sum over the fixture CSV (same technique as Task 3 Step 3
  will use against the real CSV), transfer-exclusion toggle; semantic_search
  RRF returns the exact-text chunk first (FakeEmbedder is deterministic on
  text content, so an exact-text query scores top on both the vec and FTS
  legs); passphrase canary retrievable (query = whitespace-normalized file
  content, matching how `ingest.chunk_text` stores short docs); k=50 clamps
  results to ≤5.
  Ran `uv run pytest tests/test_retrieval.py -x -q` — FAILS as expected:
  `ModuleNotFoundError: No module named 'retrieval'` (module doesn't exist
  yet — that's Step 2).
  Next: Task 3 Step 2 (implement `retrieval.py` against this test file).

- **2026-09-23 — iteration 10 (Task 3 Steps 2-4: implement retrieval.py;
  Task 3 done).**
  Checked HUMAN.md first: no new dated note since iteration 8's check;
  nothing to re-gate.
  Implemented `retrieval.py` per the Design reference and iteration 9's test
  contracts: `lookup` (all-digit ≥10-char query → PK lookup by `rows.id`;
  else FTS5 over `csv_row` chunks joined to `rows`, `bm25()` ranked, results
  reassembled preserving rank order via `_rows_by_ids`); `filter_rows`
  (whitelist `COLUMN_SQL`/`TEXT_COLUMNS`/`OPERATORS`, single `_filter_clause`
  helper shared with `aggregate`'s per-filter loop; text eq/neq use `COLLATE
  NOCASE`; `contains` is `LIKE %v% COLLATE NOCASE`; `amount` values go
  through `ingest.parse_amount_cents`; category `eq` with zero rows falls
  back to a `contains` LIKE, then to a `difflib.get_close_matches(cutoff=0.5)`
  hint if still empty); `aggregate` (`sum`+`amount` returns
  spent/received/net/count via one SQL `CASE`-summed query, other
  operation/column pairs go through a generic `{op}({col})` query;
  `transfers_excluded` always present, defaults to excluding `category !=
  'Transfer'`); `semantic_search` (vec0 KNN top-20 via `embedding match ? and
  k = 20`, FTS top-20 via the same quoted-token query as `lookup`, fused with
  a new `rrf()` k=60 reciprocal-rank-fusion helper, passed through a no-op
  `rerank()` hook, `k` clamped to 5). Injection safety for both FTS call
  sites goes through one `_quote_fts_query` helper — double-quotes each
  whitespace-split token and doubles any embedded `"`, so `NEAR(`, `OR 1=1`,
  bare quotes etc. become literal phrase search terms instead of FTS5
  operators.
  Ran `uv run pytest tests/test_retrieval.py -x -q` — all 24 passed on the
  first implementation attempt (ticked Step 2).
  Step 3 (real-data cross-check) initially FAILED: the plan's verify script
  passes a bare-year `date` filter (`{"column":"date","operator":"eq",
  "value":"2025"}`), but the Step 2 implementation only prefix-matched dates
  when the value looked like `YYYY-MM` (`re.fullmatch(r"\d{4}-\d{2}")`), so a
  4-digit year fell through to plain `date = '2025'` equality and matched
  nothing (`spent: 0.0` vs. gold `3999.15`). Generalized `_filter_clause`:
  ANY `date`+`eq` value now becomes a `LIKE 'value%'` prefix match (covers
  `"2025"`, `"2025-03"`, and a full `"2025-03-12"` equally — a full date
  under `LIKE` with no further wildcard is effectively exact). Removed the
  now-unused `re` import. Reran `tests/test_retrieval.py` — still 24 passed
  (no test exercised the old regex-only path). Reran the Step 3 script
  against the real ingested DB (`models/rag-index/locat.db`, 2673 rows, still
  present from iteration 8's real-data ingest — no re-ingest needed): SQL sum
  `3999.15` == Python-CSV gold `3999.15` to the cent. Ticked Step 3.
  Ran `uv run pytest -q` — 168 passed, 0 failures. Ticked Step 4. Task 3 is
  now fully done — Status table updated.
  Quirk for later steps to know: `filter_rows`/`aggregate` treat `date`+`eq`
  as a LEFT-prefix match, not strict equality — any code or eval query
  relying on exact single-day equality still works (a full `YYYY-MM-DD` value
  has nothing left to over-match against), but don't expect `date neq` or
  other operators to get the same prefix treatment (they don't; only `eq`
  does).
  Next: Task 4 Step 1 (failing tests for `rag_agent.py`) — Model column says
  `opus`.

- **2026-09-23 — iteration 11 (Task 4 Step 1: failing tests for rag_agent.py).**
  Checked HUMAN.md first: the only dated note is still the 2026-09-23 qwen3:8b
  catalog note under §2, already turned into plan work in iteration 2 (Task 4
  Step 8's Hugging Face fallback). Nothing new to re-gate.
  Wrote `tests/test_rag_agent.py` (24 tests) against the not-yet-written
  `rag_agent` module. No pytest-asyncio in this repo, so it follows the existing
  convention (`tests/test_subscriber_gate.py`) of calling `asyncio.run(...)`
  inside sync tests. A `FakeClient` stands in for `httpx.AsyncClient` — async
  `post(url, json=...)` that records every request body and serves scripted
  payloads; script entries may be response dicts, exceptions to raise, or async
  callables, and the LAST entry is sticky so a one-entry script answers every
  round identically (needed for the always-bad-tool-call and round-cap cases).
  The four retrieval tools run FOR REAL against a fixture SQLite db built in
  `tmp_path` by `ingest.ingest(...)` with `FakeEmbedder` over
  `tests/fixtures/transactions-small.csv` + `note.txt` + a copy of
  `data/passphrase-smoke.txt`.
  This fixes the following API contract for Step 2 to implement:
  `rag_agent.answer_question(question, *, db_path, embedder, model=None,
  api_url=None, client=None, max_rounds=4, deadline_secs=35.0) -> AgentResult`
  with fields `answer` (str), `traces` (list of `{tool, args, count,
  elapsed_ms}` — exactly the `backend` shape Step 3's RTVI frame needs),
  `timings` (dict with `rounds`, `total_ms`, `llm_ms`, `tools_ms`), `chunks`
  (semantic_search results, each with `source` + `text`), `error` (None on
  success). Module-level `TOOL_DEFS` (native format, `filter_rows.column` /
  `.operator` and `aggregate.operation` carry JSON-Schema `enum`s matching
  `retrieval`'s whitelists), `strip_think(text)`, and `FALLBACK_ANSWER` (the
  single apologetic string every error path returns — asserted by identity, not
  by substring, so the wording stays free to change in one place).
  Behaviours pinned: POST goes to `{api_url}/api/chat` with `think=False`,
  `stream=False`, `keep_alive` set, `options.temperature == 0.2`; first request
  carries system prompt + the question as the last user message + all four
  TOOL_DEFS; tool results are appended as `{"role": "tool", "content": <json>}`;
  tool `arguments` may arrive as a dict OR a JSON string (Ollama does both);
  traces accumulate across multiple tool rounds; `<think>…</think>` is stripped
  from the final content; an unknown tool name OR bad kwargs gets ONE corrective
  tool message whose content is JSON with an `error` key, and a SECOND bad call
  returns `FALLBACK_ANSWER` with `error` naming the offending tool; `max_rounds`
  caps tool-carrying requests and forces one final request with NO `tools` key
  (max_rounds=2 → exactly 3 requests); `httpx.ConnectError` /
  `httpx.HTTPStatusError` / deadline expiry → `FALLBACK_ANSWER` + `error`;
  `asyncio.CancelledError` from the client propagates out UN-CAUGHT.
  Deliberately NOT tested: the default model/api_url (they fall back to
  `config.rag_llm_model()` / `config.ollama_api_url()`, and `rag_llm_model()`
  does not exist until Step 5 — every test passes `model=` and `api_url=`
  explicitly so Step 2 can land before the five-place lockstep does, and so
  Step 8's possible `hf.co/...` tag swap needs no test edit).
  Ran `uv run pytest tests/test_rag_agent.py -x -q` — FAILS as expected:
  `ModuleNotFoundError: No module named 'rag_agent'`.
  Next: Task 4 Step 2 (implement `rag_agent.py` + `prompts/records_analyst.py`).

- **2026-09-23 — iteration 12 (Task 4 Step 2: implement rag_agent.py +
  prompts/records_analyst.py).**
  Checked HUMAN.md first: the only dated note is still the 2026-09-23 qwen3:8b
  catalog note under §2, already turned into plan work in iteration 2 (Task 4
  Step 8's `hf.co/Qwen/Qwen3-8B-GGUF:Q4_K_M` fallback). Nothing new to re-gate.
  Wrote `prompts/records_analyst.py` (`SYSTEM_PROMPT`: names the four tools and
  when to reach for each, answer ONLY from tool results, say you couldn't find
  it rather than estimating, transfers excluded from totals unless asked about,
  1–2 spoken sentences naming merchant+date or file name, money and percentages
  as words, no preamble/reasoning) and `rag_agent.py`.
  `rag_agent` shape as built: module-level `TOOL_DEFS` (4 native-format defs;
  enums are read from `retrieval.COLUMN_SQL` / `retrieval.OPERATORS` /
  `retrieval.ORDER_SQL` so the whitelists cannot drift from the SQL layer, plus
  a local `AGGREGATE_OPERATIONS`), `AgentResult` dataclass, `ToolCallError`,
  `strip_think`, `FALLBACK_ANSWER`, `answer_question`.
  Loop: `for round_index in range(max_rounds + 1)` — `tools` is included while
  `round_index < max_rounds`, so the last iteration is the forced no-tools
  compose (max_rounds=2 → exactly 3 requests). Retrieval runs via
  `asyncio.to_thread`; `_execute` raises `ToolCallError` for an unknown name,
  non-dict arguments, or `(TypeError, ValueError, KeyError, sqlite3.Error)` out
  of the retrieval call; first `ToolCallError` appends one corrective
  `{"role":"tool","content":"{\"error\": ...}"}` message, a second sets `error`
  and returns `FALLBACK_ANSWER`. Errors caught are narrow — `TimeoutError` and
  `httpx.HTTPError` only; `CancelledError` is never in an except clause, so it
  propagates (test asserts this).
  Two implementation details worth remembering:
  - The per-round body must carry `list(messages)`, NOT the live list. The test
    fake records request bodies by reference, so a shared list makes every
    recorded request show the FINAL message history — the corrective-round
    assertions (`client.requests[1]["messages"][-1]`) only pass with a copy.
    Same reasoning applies to anything else that snapshots request bodies.
  - The deadline is enforced with `asyncio.wait_for(http.post(...), remaining)`
    plus a pre-round `remaining <= 0` check. `wait_for` converts its own
    cancellation to `TimeoutError` but re-raises a `CancelledError` that came
    from the awaited coroutine, which is exactly the split the tests want.
  Ran `uv run pytest tests/test_rag_agent.py -q` — 22 passed (PROGRESS iteration
  11 said "24 tests"; the file actually holds 22 — no tests were removed).
  Ticked Step 2. Ran `uv run pytest -q` — 190 passed, 0 failures.
  Note for Step 5: `answer_question` falls back to `config.rag_llm_model()` /
  `config.ollama_api_url()` only when `model=`/`api_url=` are None.
  `config.rag_llm_model()` does not exist yet — Step 5's lockstep adds it. Until
  then every caller must pass `model=` explicitly (the tests do).
  Next: Task 4 Step 3 (failing tests for `tools.py` + pipeline).

- **2026-09-23 — iteration 13 (Task 4 Step 3: failing tests for tools.py +
  pipeline).**
  Checked HUMAN.md first: the only dated note is still the 2026-09-23 qwen3:8b
  catalog note under §2, already turned into plan work in iteration 2 (Task 4
  Step 8's `hf.co/Qwen/Qwen3-8B-GGUF:Q4_K_M` fallback). Nothing new to re-gate.
  Wrote `tests/test_tools.py` (12 tests) and `tests/test_pipeline_tools.py`
  (4 tests). `tests/test_pipeline_rag.py` still exists — Step 7 deletes it.
  API contract these pin for Step 4 to implement:
  - `tools.build_tools(db_path, embedder) -> list[FunctionSchema]` — a LIST of
    exactly one schema (not a bare schema), named `ask_financial_records`,
    `required == ["question"]`, `properties["question"]["type"] == "string"`,
    description containing "record", and `schema.handler` set (pipecat 1.7.0
    auto-registers a `FunctionSchema`'s bundled handler — no
    `register_function` call needed; `@tool_options` only if the defaults need
    overriding).
  - `tools.RAG_MESSAGE_TYPE == "locat-rag"`.
  - Handler pushes ONE `RTVIServerMessageFrame` through `params.llm.push_frame`
    whose `data` carries `type`, `query`, `answer`, `backend`, `timings`,
    `chunks`; `backend` is `result.traces` verbatim (each entry exactly
    `{tool, args, count, elapsed_ms}`) so `rag_agent`'s trace shape IS the
    frontend's route-line shape.
  - Handler delivers exactly one `result_callback` value, JSON whose key set is
    exactly `{"say_exactly"}`.
  - `rag_agent.answer_question` is called as
    `answer_question(question, db_path=..., embedder=..., ...)` — question
    positional, the rest keyword (the stub asserts on that split).
  - Missing `question` argument must NOT raise — the handler still returns a
    non-empty `say_exactly` (fallback wording is free).
  - `pipeline.build_pipeline(transport)` — `built.context.tools` holds exactly
    `ask_financial_records` when `LOCAT_RAG_DB` points at a real index, and is
    NOT_GIVEN (no tools) when it doesn't; `BuiltBot` gains `llm` and `tts`.
  Test-harness notes for whoever touches these files:
  - `FunctionCallParams` is a plain dataclass, so a hand-built one works:
    `function_name, tool_call_id, arguments, llm, pipeline_worker, context,
    result_callback` (pass `None` for `pipeline_worker`/`context` — nothing
    validates them). `RecordingLLM.push_frame` just appends.
  - `test_pipeline_tools.py` monkeypatches `pipeline.build_stt` /
    `build_llm` / `build_tts` / `build_vad_processor` to bare
    `FrameProcessor()` and passes a `SimpleNamespace(input=FrameProcessor,
    output=FrameProcessor)` as the transport — `Pipeline` only links
    processors, so nothing loads Whisper or Kokoro. Keep those stubs if the
    builders' signatures change.
  - Tool-presence is read with
    `is_given(context.tools)` + `context.tools.standard_tools`
    (`from pipecat.processors.aggregators.llm_context import is_given`);
    `LLMContext.tools` is NOT_GIVEN, not `None`, when unset.
  Ran `uv run pytest tests/test_tools.py tests/test_pipeline_tools.py -x -q` —
  FAILS as expected: `ModuleNotFoundError: No module named 'tools'`.
  Ran `uv run pytest tests/test_pipeline_tools.py -q` on its own to confirm the
  pipeline half really exercises something — 2 failed, 2 passed
  (`test_index_present_offers_only_ask_financial_records` and
  `test_built_bot_exposes_llm_and_tts` fail; the two no-index cases pass
  vacuously today because `build_pipeline` advertises no tools at all).
  Ticked Step 3.
  Next: Task 4 Step 4 (implement `tools.py`; wire pipeline + prompts +
  preflights). Heads-up for Step 4: it calls for `store.preflight(...)` in
  `bot.py`, but `store.py` has NO `preflight` yet — port it from `rag.py`'s
  equivalent (or write it) as part of that step.

- **2026-09-23 — iteration 14 (Task 4 Step 4: implement tools.py; wire
  pipeline + prompts + preflights).**
  Checked HUMAN.md first: the only dated note is still the 2026-09-23 qwen3:8b
  catalog note under §2, already turned into plan work in iteration 2 (Task 4
  Step 8's `hf.co/Qwen/Qwen3-8B-GGUF:Q4_K_M` fallback). Nothing new to re-gate.
  Wrote `tools.py`: `RAG_MESSAGE_TYPE`, `TOOL_NAME`, `TOOL_DESCRIPTION`,
  `NO_QUESTION_ANSWER`, `CALL_TIMEOUT_SECS = 45.0`, `_question`, `build_tools`.
  `build_tools` returns a one-element list holding a `FunctionSchema` whose
  bundled handler is decorated `@tool_options(timeout_secs=45.0)` —
  `cancel_on_interruption` stays at its default True, so barge-in cancels the
  handler task as designed. The handler calls `rag_agent.answer_question` via
  the MODULE attribute (`rag_agent.answer_question(...)`, never a
  `from rag_agent import answer_question`) — the tests monkeypatch the module
  attribute and a direct import would bind past the patch.
  `_question` tolerates arguments arriving as a JSON string as well as a dict
  (Ollama does both); empty/missing question short-circuits to
  `NO_QUESTION_ANSWER` without touching the backend and without pushing a
  frame.
  `pipeline.py` rewritten: `rag`/`RAGProcessor` imports and the RAG stage are
  gone; `build_records_tools()` replaces `build_rag_processor()` (same
  log-once-when-missing behaviour, now keyed on `store.has_index`
  (`config.rag_db_path()`)); `LLMContext` gets `tools=` only when tools exist
  (`LLMContext.tools` must stay NOT_GIVEN otherwise — passing `tools=None`
  is not the same thing); `BuiltBot` gained `llm` and `tts`.
  `prompts/financial_advisor.py`: dropped the "no access to accounts"
  paragraph, added the ask_financial_records paragraph (MUST call for any
  question about their own records, say a short phrase like "Let me check your
  records." first, relay `say_exactly` word for word, greetings/general
  questions answered without the tool). Spoken-style, money-as-words and
  not-a-licensed-advisor paragraphs kept verbatim.
  `store.py` gained `preflight_embed_model` (moved verbatim out of `ingest.py`,
  which now calls `store.preflight_embed_model`) plus
  `store.preflight(db_path, embed_model, api_url)` — no index → no-op. Put it
  in `store` rather than importing `ingest` from `store`: `ingest` imports
  `store`, so the other direction would be a cycle.
  `bot.py`: `import rag` → `import store`; `_preflight_rag` now calls
  `store.preflight(Path(config.rag_db_path()), ...)`; `main()` preflights BOTH
  `config.llm_model()` and `config.rag_llm_model()`. Same second preflight
  added to `bot_moq.py`, whose status line is now
  `store.stats_summary(Path(config.rag_db_path()), config.rag_data_dir())`.
  `config.py`: added `DEFAULT_RAG_LLM_MODEL = "qwen3:8b"` + `rag_llm_model()`
  (`LOCAT_RAG_LLM_MODEL`). This was already a latent NameError —
  `rag_agent.answer_question` has called `config.rag_llm_model()` since
  iteration 12. DEFAULT_LLM_MODEL is deliberately still `qwen2.5:14b`: the
  front-model flip to `llama3.2:1b` moves in lockstep with `locat.sh`,
  `scripts/run_ollama.sh`, `configure.sh` and `.env`/`env.example` in Step 5.
  Adding a brand-new var can't desync anything, since nothing else references
  `LOCAT_RAG_LLM_MODEL` yet — Step 5 wires the remaining four places.
  Ran `uv run pytest tests/test_tools.py tests/test_pipeline_tools.py
  tests/test_locat_config.py -q` — 16 passed. Ticked Step 4.
  Ran `uv run pytest -q` — 2 failed, 203 passed. Both failures are
  `tests/test_pipeline_rag.py` (`test_no_index_builds_no_rag_processor`,
  `test_existing_index_builds_rag_processor`), which assert on
  `pipeline.build_rag_processor` — the function Step 4 was supposed to remove.
  That file is explicitly "replaced by tests/test_pipeline_tools.py" and is
  deleted in Step 7, so the suite stays red until then BY DESIGN. Do not
  "fix" those two tests; delete the file in Step 7.
  Next: Task 4 Step 5 (two-model lockstep across config.py / locat.sh /
  run_ollama.sh / configure.sh / .env / env.example / print_models.py, plus the
  Ollama env exports and the three adapted test files).

- **2026-09-23 — iteration 15 (Task 4 Step 5: two-model lockstep + Ollama env).**
  Checked HUMAN.md first: the only dated note is still the 2026-09-23 qwen3:8b
  catalog note under §2, already turned into plan work in iteration 2 (Task 4
  Step 8's `hf.co/Qwen/Qwen3-8B-GGUF:Q4_K_M` fallback). Nothing new to re-gate.
  Flipped the front model to `llama3.2:1b` and added the records model
  everywhere the lockstep names:
  - `config.py`: `DEFAULT_LLM_MODEL = "llama3.2:1b"` (docstring too).
    `DEFAULT_RAG_LLM_MODEL` + `rag_llm_model()` already landed in iteration 14.
  - `locat.sh`: both defaults at lines 32–33. `model_ready()` now ANDs a new
    `tag_ready <tag>` helper over both tags, and a `missing_models()` helper
    names whichever is absent ("A and B" when both are) in all three start
    messages. `missing_models` builds a STRING, not an array — under
    `set -u` bash 3.2 errors on `${arr[*]}` for an empty array.
  - `scripts/run_ollama.sh`: both defaults, the pull is now a `for tag in ...`
    loop over both, both tags echoed, and it exports
    `OLLAMA_KEEP_ALIVE=30m`, `OLLAMA_CONTEXT_LENGTH=8192`,
    `OLLAMA_MAX_LOADED_MODELS=3` (1b + 8b + embed co-resident).
  - `configure.sh`: the `project default` catalog note moved from
    `qwen2.5:14b` (line 128) to `llama3.2:1b` (line 137); fallback literals at
    943/945 and the RAM-check default at 1156 are `llama3.2:1b`; one sentence
    added to the ".env lines for this combo" block naming
    `LOCAT_RAG_LLM_MODEL=qwen3:8b` as separately configured.
  - `.env` (gitignored, not committed): `LOCAT_LLM_MODEL=llama3.2:1b` (was
    `deepseek-r1:14b`) + `LOCAT_RAG_LLM_MODEL=qwen3:8b`. `env.example`: both
    commented defaults (`LOCAT_RAG_DB` was already there from Task 2).
  - `scripts/print_models.py`: `model_entries()` gained a `RAG-LLM` row right
    after `LLM`, same `_ollama_file` path logic. `_ollama_manifest` already
    handles `hf.co/...` tags, so Step 8's HF fallback tag will resolve too.
  Column width: "RAG-LLM" is exactly 7 chars, so the old `:<7` role column
  printed `RAG-LLMqwen3:8b` with no space. Widened to `:<8` in
  `print_models.py` (both the role and its path-continuation indent) AND in
  `client/src/panels.ts` (`padEnd(7)` → `padEnd(8)`), which renders the same
  rows from `locat-config`. Rebuilt `client/dist` this iteration
  (`cd client && npm run build`, clean).
  Tests adapted: `tests/test_locat_config.py` roles assert now
  `["STT","LLM","RAG-LLM","TTS","EMBED"]`; new
  `tests/test_print_models.py::test_model_entries_lists_both_llm_roles`
  (monkeypatches `print_models._ollama_file` to `None` so it never shells out
  to `ollama show`); new
  `tests/test_locat_sh.py::test_status_lists_both_conversation_and_records_models`
  asserting `status` prints both an `LLM llama3.2:1b` and a `RAG-LLM qwen3:8b`
  row.
  Ran the step's verify:
  `grep -rn "qwen2.5:14b\|deepseek-r1" config.py locat.sh scripts/run_ollama.sh configure.sh .env env.example`
  → only catalog/docs mentions remain, all in `configure.sh`: the catalog rows
  (128, 152–156), the reasoning-model comment + awk filter (628/630), and the
  `RECOMMEND=` RAM-sizing ladder at 1162 (a "consider X instead" suggestion for
  an oversized configured LLM, not a default — left alone deliberately; with a
  1 GB front model the warning it feeds never fires).
  `uv run pytest tests/test_locat_sh.py tests/test_print_models.py tests/test_locat_config.py -q`
  → 32 passed. Ticked Step 5.
  Ran `uv run pytest -q` → 2 failed, 205 passed. Same two by-design failures as
  iteration 14: `tests/test_pipeline_rag.py::test_no_index_builds_no_rag_processor`
  and `::test_existing_index_builds_rag_processor`, asserting on the removed
  `pipeline.build_rag_processor`. Do NOT fix them — Step 7 deletes that file.
  Next: Task 4 Step 6 (`client/src/panels.ts` `LocatRag` gains `answer`,
  `backend`, `timings` and renders one route line per backend tool call above
  the chunks; rebuild `client/dist` in the same iteration). Note Step 6 touches
  the same file this iteration already rebuilt — the `padEnd(8)` change is in
  `client/src/panels.ts` and `client/dist` is current as of this commit.

- **2026-09-23 — iteration 16 (Task 4 Step 6: frontend backend-route lines).**
  Read HUMAN.md first: still only the 2026-09-23 qwen3:8b catalog note under §2,
  already turned into plan work (Task 4 Step 8's
  `ollama pull hf.co/Qwen/Qwen3-8B-GGUF:Q4_K_M` fallback). Nothing new to re-gate.
  `client/src/panels.ts`: `LocatRag` now carries optional `answer`, `backend`
  (`RagRoute[]` = {tool, args, count, elapsed_ms}) and `timings`
  (`RagTimings` = {rounds, total_ms, llm_ms, tools_ms}) — all optional so an
  older/simpler payload still renders. Per turn the panel now paints, above the
  unchanged `<details>` chunks: one `.rag-route` line per backend tool call
  (`aggregate {"operation":"sum",...} · 5 rows · 4ms`, elapsed rounded), one
  `.rag-route.rag-timing` summary (`1840ms total · llm 1802ms · tools 38ms ·
  2 rounds`), then the composed `.rag-answer` text. The answer line exists for
  HUMAN.md §2's relay check — the human compares what the speakers said against
  the panel's `answer` word for word, so it has to be on screen.
  Field names match what `tools.py` already pushes in the `locat-rag`
  RTVIServerMessageFrame (`query`/`answer`/`backend`/`timings`/`chunks`), which
  in turn is `rag_agent.AgentResult.traces` verbatim — no shape translation on
  either side.
  `client/src/style.css`: `.rag-route` (monospace 0.72rem, 0.7 opacity,
  `overflow-wrap: anywhere` since arg JSON is long), `.rag-timing` (dimmer),
  `.rag-answer`.
  Ran the step's verify `cd client && npm run build` → clean (`tsc --noEmit`
  passed, 269 modules, built in 578ms). `client/dist` rebuilt and committed in
  THIS iteration: new `dist/assets/index-Di0p6cxP.js` +
  `index-DAH-DcF9.css`, old `index-E017z09c.js`/`index-EUwDOV5z.css` deleted,
  `dist/index.html` repointed. Note panels.ts bundles into the `index-*` chunk,
  not `main-*` (`grep rag-route` finds it there) — `main-4szzkZ8a.js` is
  unchanged by this step, that is expected, not a stale build.
  Ticked Step 6. No Python touched, so the suite is where iteration 15 left it:
  `uv run pytest -q` → 2 failed, 205 passed, both in `tests/test_pipeline_rag.py`
  asserting on the removed `pipeline.build_rag_processor`. Still by design — do
  NOT fix them, Step 7 deletes that file.
  Next: Task 4 Step 7 (delete `rag.py`, `rag_processor.py`, `tests/test_rag.py`,
  `tests/test_rag_processor.py`, `tests/test_pipeline_rag.py`; verify grep finds
  no `import rag`/`rag_processor` — `rag_agent` is a different module and must
  not be matched — then the FULL suite must go green, which those two deletions
  should accomplish).

- **2026-09-23 — iteration 17 (Task 4 Step 7: delete the old RAG stack).**
  Read HUMAN.md first: still only the 2026-09-23 qwen3:8b catalog note under §2,
  already turned into plan work (Task 4 Step 8's
  `ollama pull hf.co/Qwen/Qwen3-8B-GGUF:Q4_K_M` fallback, which also tells the
  next iteration to propagate the full `hf.co/...` tag through the lockstep).
  Nothing new to re-gate.
  `git rm` on the five files Step 7 names: `rag.py`, `rag_processor.py`,
  `tests/test_rag.py`, `tests/test_rag_processor.py`, `tests/test_pipeline_rag.py`.
  Checked before deleting that every `import rag` / `rag_processor` /
  `RAGProcessor` / `build_rag_processor` reference lived inside those same five
  files — nothing in `pipeline.py`, `bot.py`, `bot_moq.py`, `locat.sh`, or
  `client/` pointed at them any more (Step 4 had already cut the pipeline stage).
  Step's verify:
  `grep -rn "import rag\b\|from rag import\|rag_processor" --include="*.py" . | grep -v ".venv"`
  → empty (`rag_agent.py` correctly not matched — `import rag\b` has the word
  boundary, and nothing does `from rag import`).
  `uv run pytest -q` → **172 passed**, 0 failed. The two by-design failures that
  iterations 14–16 carried (`tests/test_pipeline_rag.py::test_no_index_builds_no_rag_processor`
  and `::test_existing_index_builds_rag_processor`, asserting on the removed
  `pipeline.build_rag_processor`) are gone with the file. The suite is fully
  green again for the first time since iteration 13; test count dropped 205 → 172
  because the deleted files' cases were already ported into
  `tests/test_ingest.py`, `tests/test_retrieval.py`, `tests/test_store.py`, and
  `tests/test_pipeline_tools.py` in Tasks 2–4. Ticked Step 7.
  One stale comment fixed alongside: `configure.sh:498` said the embed-model
  tag match works "like rag.py's preflight does" and now names `store.py`
  (`store.preflight_embed_model`, the function that actually does the
  `:latest`-tolerant match). No behaviour change.
  Still-outstanding references to `rag.py` are in `README.md` (lines 270, 298 —
  `uv run python rag.py stats` and "implementation detail of `rag.py`") and are
  deliberately left for **Step 9**, which rewrites that whole retrieval section;
  `docs/superpowers/specs/2026-09-02-frontend-rag-lifecycle-design.md` and
  `ralph/PLAN-v2.md` mention it as historical record and stay as-is.
  No background processes started this iteration (no Ollama, no bot), so no
  `./locat.sh stop` needed.
  Next: Task 4 Step 8 (`ollama pull qwen3:8b`, large sanctioned download; on a
  manifest/not-found error fall back to
  `ollama pull hf.co/Qwen/Qwen3-8B-GGUF:Q4_K_M` and propagate that exact tag
  through `config.py` / `locat.sh` / `scripts/run_ollama.sh` / `.env` /
  `env.example`, recording the tag actually used here; then the `/api/chat`
  `think:false` probe must return no `thinking` field and no `<think>` in
  content).

- **2026-09-23 — iteration 18 (Task 4 Steps 8-9: qwen3 pull, think probe, README).**
  Read HUMAN.md first: still the single 2026-09-23 qwen3:8b catalog note under
  §2, already turned into plan work in iteration 2. This iteration ANSWERED it
  and appended a dated reply under it — see below.
  **Step 8.** Ollama was already running when this iteration started (not
  started by the loop — iterations 16/17 started nothing, so it's the human's
  `ollama serve`); left it running, no `./locat.sh stop` needed.
  `ollama pull qwen3:8b` → **succeeded from the plain Ollama registry**, 5.2 GB,
  `success`. **The `hf.co/Qwen/Qwen3-8B-GGUF:Q4_K_M` fallback was NOT used and
  no lockstep string changed** — `LOCAT_RAG_LLM_MODEL` stays the bare tag
  `qwen3:8b` everywhere (`config.DEFAULT_RAG_LLM_MODEL`, `locat.sh`,
  `scripts/run_ollama.sh`, `.env`, `env.example`). **Evals and Task 5 must use
  the string `qwen3:8b`.**
  Root cause of the human's note: `qwen3:8b` was never missing from the
  registry — the "catalog" they checked is `configure.sh`'s own hardcoded model
  menu, which only ever listed the front-model options. The records model is
  never picked from that menu; it's `LOCAT_RAG_LLM_MODEL`. Wrote that answer
  into HUMAN.md §2 as a dated note under theirs so the gate doesn't stall on it.
  Probe (the step's verify command, verbatim):
  `curl -s localhost:11434/api/chat -H 'Content-Type: application/json' -d '{"model":"qwen3:8b","messages":[{"role":"user","content":"hi"}],"think":false,"stream":false}'`
  → `{"model":"qwen3:8b",...,"message":{"role":"assistant","content":"Hello! How can I assist you today? 😊"},"done":true,...}`
  — **no `thinking` key, no `<think>` in content.** Ran a control with
  `"think":true` on the same prompt: `thinking` IS populated there, so
  `think:false` is genuinely suppressing it rather than the model just not
  reasoning on a trivial prompt. So **no `/no_think` soft switch needed** in
  `prompts/records_analyst.py`; `rag_agent.strip_think()` stays as belt-and-braces.
  `/api/show` confirms `capabilities: ['completion','tools','thinking']`,
  `parameter_size 8.2B`, `quantization_level Q4_K_M`, family `qwen3` — tool
  calling is advertised, which the back agent depends on.
  Installed model list for reference (`/api/tags`): `llama3.2:1b`, `qwen3:8b`
  (new), `nomic-embed-text:latest`, plus unrelated older pulls. Disk after the
  pull: 230 GiB free.
  **Step 9.** Rewrote README's retrieval section (heading renamed
  `## RAG (documents)` → `## Retrieval (your transactions and documents)`):
  two-model split (1b converses + relays, qwen3:8b records analyst), the single
  `ask_financial_records` tool, a 4-row table of the analyst's typed tools and
  what each is for, the one-SQLite-file story (typed rows + FTS5 + sqlite-vec,
  incremental sha256 ingest, rebuild-on-embed-model-change), the row-as-prose
  serialization plus monthly rollups, and an explicit "the old
  `chunks.jsonl`/`embeddings.npy`/`manifest.json` are inert, left on disk"
  paragraph. `./locat.sh index-rag` / `status` text unchanged as the plan
  requires. Canary trick kept, but its evidence line now points at the browser
  panel's backend route lines instead of the dead `grep "rag:"` log line, and
  the inspect-the-index line is now `uv run python ingest.py stats` /
  open `models/rag-index/locat.db` in sqlite3 (was `uv run python rag.py stats`
  / grep chunks.jsonl — `rag.py` was deleted in iteration 17).
  Config table: `LOCAT_LLM_MODEL` default corrected `qwen2.5:14b` →
  `llama3.2:1b`; new `LOCAT_RAG_LLM_MODEL` and `LOCAT_RAG_DB` rows;
  `LOCAT_RAG_INDEX_DIR` no longer claims the numpy filenames. `LOCAT_RAG_TOP_K`
  row kept but labelled honestly as a **dead knob** — `grep -rn RAG_TOP_K
  --include="*.py"` finds it ONLY in `config.py` (the getter and its default);
  nothing reads it since the always-on injection was retired, and the tools cap
  themselves at 5. Left the getter in place rather than removing it (out of
  scope here; if a later task wants it gone, `config.rag_top_k`,
  `DEFAULT_RAG_TOP_K`, and the `env.example` line 14 comment are the three spots).
  Two other stale spots fixed in the same file since they now contradicted the
  section: the components table's "Language model | Qwen2.5-14B-Instruct" row,
  and the setup instructions for `scripts/run_ollama.sh` which said it pulls
  "`qwen2.5:14b` by default, ~9 GB" (it pulls both tags now, ~1.3 GB + ~5.2 GB).
  Roadmap item 1 ("Document RAG") struck through as shipped.
  Verifies run: `grep -n "ask_financial_records\|LOCAT_RAG_LLM_MODEL\|sqlite" README.md`
  → 8 hits, all in the retrieval section or the config table (step's expected
  result). `grep -rn "rag\.py\|rag_processor\|qwen2.5:14b\|deepseek-r1" README.md`
  → empty. `uv run pytest -q` → **172 passed**, unchanged from iteration 17
  (no Python touched). No `client/src/` change, so no rebuild needed.
  **Task 4 is now fully checked.** Next: Task 5 is `gated: HUMAN.md §2` (ears-on
  voice test — the loop cannot do it), so the next iteration should skip it and
  start **Task 6 Step 1** (build `evals/queries.jsonl`: ~20 gold retrieval
  entries computed from the REAL CSV with a throwaway script, never guessed,
  plus ~4 chit-chat delegation-precision probes). Everything it needs is live:
  the real DB is indexed (2673 rows), and all three models — `llama3.2:1b`,
  `qwen3:8b`, `nomic-embed-text` — are pulled.

- **2026-09-23 — iteration 19 (Task 6 Step 1: `evals/queries.jsonl`).**
  HUMAN.md re-read first: no new dated note under an unchecked Gate (the
  qwen3:8b note under §2 was already answered in iteration 18; §1 and §3 are
  still unchecked but that's expected — nothing to turn into plan work this
  iteration).
  Ollama was already running (not started by this iteration, so no
  `./locat.sh stop` needed) with `llama3.2:1b`, `qwen3:8b`,
  `nomic-embed-text:latest` all pulled, and the real DB already indexed at
  `models/rag-index/locat.db` (2673 rows, 3246 chunks: 2673 csv_row + 572
  rollup + **1 doc** — the passphrase-smoke.txt canary).
  Built `evals/queries.jsonl` — 24 lines: 5 lookup / 5 filter / 5 aggregate /
  5 semantic / 4 chitchat. Every gold value was computed by calling
  `retrieval.py`'s real functions (`lookup`, `filter_rows`, `aggregate`,
  `semantic_search` with `ingest.OllamaEmbedder`) against the live indexed DB
  in a throwaway REPL session (not guessed), and two aggregate values
  (groceries-2025 sum = 3999.15, restaurants-2025 count = 186) were
  additionally cross-checked against a raw `csv.DictReader` sum over
  `data/Transactions_2026-09-08T18-09-28.csv` — exact match to the cent, per
  the plan's spot-check instruction.
  Design choices future steps (Step 2 harness, Step 3 full run) need to know:
  - **Lookup gold picks only merchants with exactly one CSV row** (`eBay`,
    `Paulie's Delicatessen`, `Symmetry Jewelers`, `United Airlines`, plus one
    18-digit-id lookup) so `expected_source_ids` is an unambiguous single ID.
    Deliberately avoided a "Navy Federal" style query — that merchant has 37
    rows and bm25 ties would make the gold set non-deterministic.
  - **Filter gold uses only ONE filter column per call** (matches
    `filter_rows`'s actual signature — it cannot combine category+date in one
    call). `filter-2-march-2025-top5` sorts by `amount_abs` across ALL rows
    that month, so it legitimately returns paychecks/mortgage alongside
    debits — the query text says "transactions", not "expenses", specifically
    to avoid gold/agent mismatch on that point.
  - **Aggregate gold has `expected_source_ids: []`** (aggregate has no
    single-row source concept) and `expected_answer` holds just the bare
    number as a string for containment matching in Step 2's normalized-number
    scorer. One sign-convention flag for Step 2: `aggregate-4-coffee-avg`'s
    raw `retrieval.aggregate(operation="avg", ...)` result is **-14.5752**
    (debits are negative `amount_cents`); the gold answer is written as the
    unsigned magnitude `"14.58"` since a spoken answer would say "about
    fourteen dollars," never "negative fourteen dollars." Step 2's scorer
    must normalize sign (strip `-`) before comparing, not just round.
  - **Semantic gold** was picked by actually running `semantic_search` with
    the real `nomic-embed-text` embedder against 5 candidate NL phrasings each
    and keeping only the ones whose target row landed in the top-4 (all 4 real
    ones are rank 1 except the dentist query, which is rank 2 behind a
    rollup chunk — both still count as a "hit" for source-id recall). The
    passphrase canary is the **only** query in the whole file that hits the
    lone `doc` chunk; `expected_source_ids: ["passphrase-smoke.txt"]` uses the
    file basename (string) since doc chunks have no `row_id`, unlike csv_row
    chunks which use the integer row_id — Step 2's scorer needs to branch on
    type when comparing IDs.
    `semantic-5-refusal-yachts` deliberately has `expected_source_ids: []` and
    a `"refusal": true` flag (extra field beyond the plan's literal schema,
    harmless in JSONL) — no yacht category/rows exist in the data, so this is
    the "not in the data" probe; retrieval still returns 4 loosely-related
    rollup/csv_row results (nothing about yachts specifically), so refusal
    must be judged at the e2e answer stage, not by an empty retrieval result.
  - **Chitchat probes** are the 4 the plan specified verbatim, unchanged.
  Verify run: `uv run python -c "import json; qs=[json.loads(l) for l in
  open('evals/queries.jsonl')]; assert len(qs)>=22; from collections import
  Counter; print(Counter(q['type'] for q in qs))"` →
  `Counter({'lookup': 5, 'filter': 5, 'aggregate': 5, 'semantic': 5,
  'chitchat': 4})` — matches the step's expected shape exactly.
  No Python/`client/src/` files touched, so `uv run pytest -q` and
  `client && npm run build` were not re-run this iteration (nothing to
  regress); Task 6 Step 4 will run the full suite anyway before that task is
  marked done. No background processes started, so no `./locat.sh stop`.
  **Next: Task 6 Step 2** — build `evals/run_evals.py` (four stages:
  retrieval / toolcall / e2e / isolate — see PLAN.md for the full spec) plus
  `tests/test_evals.py` (offline: scoring, arg-subset matching, number
  normalization — remember the sign-strip note above, and relay-fidelity
  scorer). Everything Step 2 needs already exists: `evals/queries.jsonl` (this
  iteration), `rag_agent.TOOL_DEFS`, `retrieval.py`, `prompts/`.

- **2026-09-23 — iteration 20 (Task 6 Step 2: `evals/run_evals.py` harness + `tests/test_evals.py`).**
  HUMAN.md re-read first: no new dated note under an unchecked Gate (§1 and §3
  remain unchecked as expected — nothing to turn into plan work).
  Wrote `tests/test_evals.py` FIRST (32 cases) against `evals.run_evals`
  (namespace package import, same pattern `scripts/` already uses — no
  `__init__.py` needed since `pythonpath = ["."]`); confirmed it failed
  (`ImportError: cannot import name 'run_evals'`) before writing any
  implementation.
  Implemented `evals/run_evals.py`: pure offline scorers (`extract_numbers` /
  `primary_number` — small English number-word parser for spoken dollars-
  and-cents, bare integers, and digit form, all as magnitudes since a spoken
  answer never says "negative"; `answer_contains` — text-token overlap +
  numeric closeness; `is_refusal`; `args_subset` — order-independent for the
  `filters` list; `source_ids_from_result` / `source_id_hit`; `relay_fidelity`
  / `relay_ok` at the 0.95 threshold) plus the four stage functions
  (`run_retrieval_stage`, `run_toolcall_stage` with FRONT streaming
  OpenAI-compat delegation + BACK native `/api/chat` tool selection,
  `run_e2e_stage` with a relay leg through the front model, `run_isolate_stage`
  pasting gold rows into the BACK prompt) and a `--stage
  retrieval|toolcall|e2e|isolate|all` CLI.
  First offline run had 2 failures — the money-word regex's number-phrase
  group was unbounded (`[a-z]+(?:[\s-]+(?:and\s+)?[a-z]+)*?` swallowed
  preceding non-number words like "you paid"). Fixed by building the phrase
  regex from an explicit alternation of the number-word vocabulary instead of
  bare `[a-z]+`. All 32 offline tests green after that
  (`uv run pytest tests/test_evals.py -q`).
  Two bugs only surfaced by manually smoke-testing `run_retrieval_stage`
  against the REAL indexed DB (Ollama was already running, not started by
  this iteration — no `./locat.sh stop` needed) — worth doing even though the
  step's literal verify command is only the offline test:
  1. `ingest.OllamaEmbedder()` takes required `base_url`/`model` positional
     args (see `pipeline.py:75`, `ingest.py:455`); `_amain` called it bare.
     Fixed to `ingest.OllamaEmbedder(config.ollama_api_url(),
     config.embed_model())`.
  2. `source_ids_from_result` compared semantic-search doc/rollup chunks'
     full `source` path against `queries.jsonl`'s basename convention
     (`"passphrase-smoke.txt"`) — always missed. Fixed to `Path(source).name`
     for non-`csv_row` chunk types (csv_row chunks still use the integer
     `row_id`).
  3. Four filter queries' `expected_args_subset` in `evals/queries.jsonl`
     (`filter-1/2/4/5`, all "five biggest ..." or "over $1000" queries) were
     missing `order_by`/`limit` — without them `retrieval.filter_rows`
     defaults to `order_by="date"`, which doesn't reproduce the gold rows
     iteration 19 actually computed with `order_by="amount_abs"`. Added
     `"order_by": "amount_abs", "limit": 5` to those four entries; re-verified
     each against `retrieval.filter_rows` directly — exact id-list match, not
     just a hit. `filter-3-restaurants-recent5` needed no change (its default
     `order_by="date"` was already the gold sort). This also sharpens the
     BACK-selection `args_subset` check in `--stage toolcall`: qwen3:8b now
     has to pick the right sort too, not just the right filter.
  4. `evals/run_evals.py` imported repo-root modules (`config`, `ingest`, …)
     without putting the repo root on `sys.path` — works under pytest
     (`pythonpath = ["."]`) but `uv run python evals/run_evals.py` (the
     literal command Step 3 and the plan's docstring both use) failed with
     `ModuleNotFoundError: No module named 'config'` since `evals/` is the
     script's own directory, not the repo root. Fixed with an explicit
     `sys.path.insert(0, str(REPO_ROOT))` before those imports. Verified
     `uv run python evals/run_evals.py --stage retrieval` end-to-end against
     the real DB: 100% hit rate on all four types (lookup/filter/
     aggregate/semantic) after the fixes above.
  Did NOT run `--stage toolcall`/`e2e`/`isolate` this iteration — those need
  many live llama3.2:1b/qwen3:8b round trips and are Task 6 Step 3's job
  ("Full run"), not Step 2's ("harness + offline unit test"). They are
  implemented and the retrieval stage's real-DB run is a good sign the
  request/response shapes line up, but they are UNVERIFIED against live
  models — the next iteration (Step 3) is where a first real run, and any
  bugs in the streaming SSE parsing or the relay-leg message shape, will
  surface.
  Full suite: `uv run pytest -q` → **204 passed** (172 + 32 new eval tests),
  no regressions. No `client/src/` touched, so no rebuild needed.
  **Next: Task 6 Step 3** — `uv run python evals/run_evals.py 2>&1 | tail -40`
  for real (needs llama3.2:1b + qwen3:8b + nomic-embed-text + the indexed DB,
  all already in place), paste the report table into this file, then
  `./locat.sh stop`. Watch especially for: the FRONT stream's SSE
  `data:`-line parsing actually matching Ollama's OpenAI-compat streaming
  format (never exercised against a live server yet), and whether qwen3:8b's
  BACK-selection tool calls come back with `arguments` as a dict or a JSON
  string (rag_agent.py's `_parse_arguments` handles both but `_back_selection`
  here does its own inline parsing — check it does too, verbatim, if the tool
  call shape differs from what's expected).

- **2026-09-23 — iteration 21 (Task 6 Steps 3-4: full eval run against live
  models + full suite check).**
  HUMAN.md re-read first: no new dated note under an unchecked Gate (§1 and §3
  still unchecked as expected; §2's 2026-09-23 note was already answered by
  iteration 18 and needs no further plan work — it's the ears-on gate itself
  that's still open, nothing to convert).
  Ollama was already running with `qwen3:8b`, `llama3.2:1b`,
  `nomic-embed-text:latest` all pulled; `uv run python ingest.py stats --bare`
  confirmed the real DB already indexed (3246 chunks from 3 files) — no
  `./locat.sh start`/`stop` needed since nothing was started this iteration.
  Ran `uv run python evals/run_evals.py 2>&1 | tail -40` for real. Completed
  cleanly, no exceptions, all four stages printed:

  ```
  == Retrieval accuracy by type ==
  lookup | 100%
  filter | 100%
  aggregate | 100%
  semantic | 100%

  == Delegation / ack / back selection ==
  delegation_recall | 0.95
  delegation_precision | 0.0
  ack_rate | 0.0
  back_correct_tool_rate | 0.6
  back_args_subset_rate | 0.2

  == Answer accuracy / relay fidelity ==
  answer_accuracy | 0.05
  relay_fidelity_avg | 0.5991200539718954

  == Latency (ms) ==
  lookup-1-by-id | 2707.37 | 287.84579201601446
  lookup-2-paulies | 1928.65 | 229.43279216997325
  lookup-3-symmetry | 2036.74 | 560.0287080742419
  lookup-4-ebay | 2433.0 | 249.55295911058784
  lookup-5-united | 1549.34 | 161.14779096096754
  filter-1-groceries-top5 | 1964.45 | 393.7239160295576
  filter-2-march-2025-top5 | 6671.13 | 810.594332870096
  filter-3-restaurants-recent5 | 1919.13 | 415.5220000538975
  filter-4-over-1000 | 4705.73 | 473.39550009928644
  filter-5-coffee-top5 | 1930.93 | 249.48083283379674
  aggregate-1-groceries-sum-2025 | 2227.59 | 162.80649998225272
  aggregate-2-restaurants-count-2025 | 6125.66 | 177.31916601769626
  aggregate-3-total-spend-2025 | 4518.67 | 331.4227079972625
  aggregate-4-coffee-avg | 1947.71 | 645.9252501372248
  aggregate-5-shopping-max | 2418.0 | 191.6127090808004
  semantic-1-passphrase-canary | 553.88 | 147.39920804277062
  semantic-2-shipped-package | 2730.71 | 157.85183291882277
  semantic-3-dentist | 2584.34 | 176.02408304810524
  semantic-4-hotel | 3329.49 | 163.3569998666644
  semantic-5-refusal-yachts | 2005.06 | 1686.518207890913

  == Isolate (gold rows pasted into BACK prompt) ==
  lookup-1-by-id | model failure
  lookup-2-paulies | retrieval failure
  lookup-4-ebay | retrieval failure
  lookup-5-united | model failure
  filter-1-groceries-top5 | retrieval failure
  filter-2-march-2025-top5 | retrieval failure
  filter-3-restaurants-recent5 | model failure
  filter-4-over-1000 | model failure
  filter-5-coffee-top5 | model failure
  aggregate-1-groceries-sum-2025 | retrieval failure
  aggregate-2-restaurants-count-2025 | retrieval failure
  aggregate-3-total-spend-2025 | retrieval failure
  aggregate-4-coffee-avg | retrieval failure
  aggregate-5-shopping-max | model failure
  semantic-1-passphrase-canary | retrieval failure
  semantic-2-shipped-package | model failure
  semantic-3-dentist | model failure
  semantic-4-hotel | model failure
  semantic-5-refusal-yachts | model failure
  ```

  Retrieval is perfect across all four types — the SQL/FTS/vec0 layer (Tasks
  2-3) is solid. The toolcall/e2e numbers are low
  (delegation_precision/ack_rate 0.0, answer_accuracy 0.05), which matched the
  exact risk iteration 20 flagged as unverified (FRONT SSE parsing never
  exercised live) closely enough to warrant a manual check before trusting the
  report: hand-curled `llama3.2:1b` against `/v1/chat/completions` with the
  real front prompt and tool schema, streaming, both for a data query ("what
  did I pay Navy Federal?") and a chitchat probe ("how are you today"). Both
  came back as a SINGLE chunk whose `delta` already contains the full
  `tool_calls` array with no preceding `content` delta — confirms the harness's
  SSE parsing is correct and `ack_rate`/`delegation_precision` are measuring
  real model behavior, not a parsing bug: `llama3.2:1b` never speaks an ack
  phrase before calling the tool (it emits the tool call in the very first
  chunk), and it calls `ask_financial_records` even on pure chitchat like "how
  are you today" despite the prompt's "greetings and general questions are
  answered normally without the tool" instruction. So the low
  delegation_precision/ack_rate/answer_accuracy numbers are genuine findings
  about the front model/prompt, not a harness bug — safe to hand to Task 7's
  human gate as-is.
  The `isolate` stage's near-universal "retrieval failure"/"model failure"
  split (no passes) reads oddly at a glance, but per the plan's own isolate
  semantics this is scored per e2e-failure only (every failing e2e case gets
  re-run with gold rows pasted into the BACK prompt: pass ⇒ retrieval was the
  problem, fail ⇒ the 8B still got it wrong even with perfect context) — given
  answer_accuracy is 0.05, nearly every query IS an e2e failure, so nearly
  every row appearing here is expected, not a new bug.
  `uv run pytest -q` (Step 4): **204 passed**, no regressions. No
  `client/src/` touched, no rebuild needed. No background processes started
  this iteration (Ollama was already running from before), so no
  `./locat.sh stop` was needed either.
  Task 6 is now fully done (Status table updated). Task 7 (`gated: HUMAN.md
  §3`) is the only remaining work — the human needs to read the report above
  and judge whether these numbers (strong retrieval, weak delegation/ack/relay
  on the front 1b) are acceptable for the demo or need prompt/contingency
  changes (e.g. the plan's own deterministic-relay fallback, or sharpening the
  front tool description/examples to fix delegation precision and the missing
  ack). Every other task (1-6) is done; only gated/blocked work remains.
  **Next:** nothing left for the loop to pick up autonomously — Tasks 5 and 7
  are both `gated:` on HUMAN.md sections that only the human can clear. This
  iteration ends with `<promise>HUMAN_GATE</promise>`.

- **2026-10-01 — iteration 22 (Task 5 gate cleared by human; bookkeeping only).**
  HUMAN.md re-read first: §2's Gate box is now CHECKED by the human (voice
  smoke test — delegation, ack, relay, canary, barge-in, refusal) with NO new
  dated failure note under it, so per the gate protocol it passed and needs no
  plan work. §1 also checked. §3 (eval results review) still unchecked, no
  dated note — stays gated, nothing to convert.
  Work this iteration is bookkeeping only: ticked Task 5's gate checkbox in
  `ralph/PLAN.md` (verification IS the human's checked §2 box — the loop
  cannot and did not re-run the ears-on test), set Status row 5 to done,
  refreshed the `_Updated:_` line. No code, no tests, no processes started
  (nothing to `./locat.sh stop`), `git status` was clean at start.
  Remaining unchecked work is exactly one step: Task 7 `gated: HUMAN.md §3` —
  the human reads iteration 21's eval report (pasted above: retrieval 100%
  across all four types; delegation_recall 0.95 but delegation_precision 0.0,
  ack_rate 0.0, answer_accuracy 0.05, relay_fidelity_avg 0.60 — the front 1b
  never acks before the tool call, over-delegates on chitchat, and paraphrases
  the relay) and either signs off or leaves a dated note under §3 naming which
  contingency to apply (deterministic relay via `run_llm=False`, sharpened
  front tool description, flat aggregate params). This iteration ends with
  `<promise>HUMAN_GATE</promise>`.

- **2026-10-12 — bugfix pass (human-driven session, not a loop run).** First real
  voice session surfaced four bugs; all root-caused from
  `.locat/logs/bot-20261012-110436.log` and fixed directly:
  1. Transcript duplication — pipecat TTS emits pre-synthesis (spoken:false) and
     post-synthesis text frames per utterance, each becoming a `bot-output` with
     a different segment_id; `transcript.ts` now drops the spoken:false half
     (`client/dist` rebuilt).
  2. Double-encoded tool result — `tools.py` passed `json.dumps(...)` to
     `result_callback`, which pipecat re-serializes; now passes the dict.
  3. Relay made deterministic — the handler speaks the records agent's answer
     itself (`TTSSpeakFrame`) and suppresses the 1b follow-up completion with
     `FunctionCallResultProperties(run_llm=False)`; canned filler
     ("Let me check your records.") restored on `on_function_calls_started`
     (pipeline.FILLER_PHRASE); front prompt's verbatim-relay/ack clauses dropped.
     Motivation beyond the encoding bug: the 1b parrots Ollama's tool template
     under fragmented STT input (its cancelled first call literally contained
     the template text "Given the following functions, please respond with a
     JSON..."), so it is now out of the answer path entirely.
  4. Date grounding — `rag_agent.answer_question` takes `today` (default real
     date; evals pin `PINNED_TODAY=2026-10-12`); questions arrive as
     "Today's date is …. The transaction records run from … to ….\nQuestion: …"
     via new `store.date_range()`; records_analyst prompt resolves relative
     dates and prefers `between` over open-ended comparisons. ("last August"
     had resolved to 2023-08.)
  Evals extended: 3 relative-date gold queries (incl. one out-of-range refusal),
  2 fragmented multi-message delegation probes, `fragmented_delegation_recall`
  metric; relay leg removed from e2e (deterministic by construction).
  `think_filter` note: never existed on this branch — nothing to remove.
  `uv run pytest -q`: **211 passed**. HUMAN.md §2 re-opened for a post-fix
  ears-on re-check. Eval rerun report to be appended below.

- **2026-10-12 — post-fix eval rerun (PINNED_TODAY=2026-10-12, 23 gold + 4 chitchat
  + 2 fragmented).** Two runs: after the four bugfixes, then after a
  records_analyst prompt pass (money as digits — SpokenTextFilter verbalizes at
  TTS; mandatory semantic_search for document questions; spoken-year mapping;
  date-filter reminder). Final numbers vs 2026-10-01 baseline:
  retrieval 100% all five types (was 100%/four types) · answer_accuracy **0.35**
  (was 0.05) · delegation_recall 1.0 (0.95) · fragmented_delegation_recall 1.0
  (new) · delegation_precision 0.0 (0.0) · ack_rate 0.0 — now moot, canned filler
  covers it · relay: deterministic by construction (was 0.60) ·
  back_correct_tool_rate 0.52 (0.6) · back_args_subset_rate 0.17 (0.2) ·
  backend total latency ~0.8–7.3 s/query.
  Reading: the four session bugs are fixed and verified; the remaining ceiling is
  qwen3:8b's own tool selection and argument construction (isolate attributes 10
  of 15 failures to agent-side retrieval, 5 to composing). delegation_precision
  0.0 (1b tool-calls on chitchat) persists — harmless now that the backend answers
  grounded or refuses, but it costs a needless backend round-trip on small talk.
  Candidate next levers, by weight: few-shot tool-call examples in
  records_analyst; swapping LOCAT_RAG_LLM_MODEL to the already-pulled
  qwen2.5:14b; enabling qwen3 thinking for the back model (latency cost masked by
  the filler). Model choice is a human decision — not applied.

- **2026-10-12 — records-model decision: `qwen2.5:14b` (human call).** Matrix run
  with the new `LOCAT_RAG_LLM_THINK` knob (config.rag_llm_think → native
  `think` param; agent deadline 35 s → 60 s when thinking; tool outer timeout
  75 s):
  * qwen3:8b, no thinking (baseline): answer_accuracy 0.35, back tool 0.52,
    args 0.17, backend 0.8–7.3 s.
  * qwen3:8b + thinking: DISQUALIFIED — single tool-selection calls overran a
    180 s HTTP timeout twice (overthinking); unusable behind a voice filler
    regardless of accuracy. Harness now records model timeouts as failed rows
    instead of crashing.
  * qwen2.5:14b: answer_accuracy 0.35, back tool 0.74, args 0.22,
    backend 2.3–13.4 s. Human tested it live and picked it.
  Default moved to qwen2.5:14b in lockstep (config.py, locat.sh,
  run_ollama.sh, configure.sh fallback, env.example, .env) + README + module
  docstrings made model-neutral. The matrix also flushed out a real crash:
  `parse_amount_cents` raised `decimal.InvalidOperation` (not in `_execute`'s
  catch tuple) on non-numeric model args — now tolerates `$1,000` formatting
  and raises ValueError (becomes a corrective round). 215 tests green.
  Note: answer_accuracy parity (0.35 = 0.35) with better tool rates means the
  residual failures are argument quality and routing variance — next lever is
  few-shot examples in records_analyst, measured against this baseline.

- **2026-10-12 — final verification run on the shipped default (no env
  overrides):** answer_accuracy 0.35, back tool 0.74, args 0.22, delegation
  recall 0.96, fragmented 1.0, backend mean 5.3 s / max 14.3 s — reproduces the
  matrix's qwen2.5:14b column. Full report: this entry's numbers; raw log was in
  the session scratchpad.

- **2026-10-12 — workshop cut: collapsed to a single-model cascade (human
  decision; conference workshop ≤2 weeks, attendees on own laptops).**
  The two-model split is retired: qwen2.5:14b is THE pipeline LLM holding the
  four retrieval FunctionSchemas directly (tools.py rewritten; `PARAMS` is the
  schema source of truth shared with evals). Answers stream to TTS (the old
  out-of-pipeline agent returned whole strings); the canned filler stays.
  Deleted: rag_agent.py, prompts/records_analyst.py (merged into
  financial_advisor.py — `system_prompt(today, date_range)` grounds dates at
  build time), tests/test_rag_agent.py, LOCAT_RAG_LLM_MODEL/THINK knobs,
  print_models RAG-LLM row (roles back to STT/LLM/TTS/EMBED), panel
  query/answer/backend/timings fields (locat-rag is now one message per tool
  call: tool/args/count/elapsed_ms/chunks; dist rebuilt).
  **pipecat stays 1.7.0, ceiling `<1.8` added with reason:** every release
  1.8.0+ has core dep `loudness` and moq-ffi-based `moq-rs>=0.4` — neither has
  EVER shipped an Intel-mac wheel, so 1.8+ ends Intel-mac support. Human wants
  the Intel machine testable; upgrade is a post-workshop decision.
  The naive chunk-and-embed RAG story is now a README paragraph (git history
  pointer), not code. 186 tests green. Eval reruns (cascade 14b + the
  documented 7b small-hardware floor) appended below when done.

- **2026-10-12 — cascade eval results (PINNED_TODAY, 23 gold + 4 chitchat +
  2 fragmented).** qwen2.5:14b: answer 0.30, first-turn tool 0.70, args 0.17,
  TTFT 2.1 s, loop mean 8.5 s (max 36.9 s — one runaway multi-round query).
  qwen2.5:7b: answer 0.35, tool 0.48, args 0.22, TTFT 0.6 s, loop mean 3.9 s.
  **Cascade wins the split outright on behavior:** no_tool_on_chitchat 1.0
  (was 0.0 — the 1b's worst habit, gone), fragmented recall 1.0, answers now
  stream. Accuracy ceiling (~0.30–0.35 across every config tried) is argument
  construction, not model size or topology — isolate still attributes most
  failures to agent-side retrieval args. Next lever unchanged: few-shot
  tool-call examples in the system prompt. 7b documented in README as the
  16 GB floor; default stays 14b (human can flip with one env var).

- **2026-10-12 — tool-call construction fixes, eval doubled.** Probed four
  failing gold queries; causes split four ways and two were OUR layer: (1)
  `between "2025-03".."2025-03"` matched nothing — literal string compare; eq
  had prefix magic, between didn't. retrieval.py now expands year/month
  prefixes for between and pads lte/gt period bounds. (2) Model guessed
  `merchant contains "grocery"` because it can't know category vocabulary —
  the 48 category names now ride in the system prompt grounding line
  (store.categories(), build-time, cache-stable). (3) Routing: "when did I
  ship a package" went to lookup (word absent from rows) — few-shot example
  added routing activity/event questions to semantic_search. (4) qwen2.5
  answered one query IN THAI — added "Always answer in English, if the
  question was asked in English." Plus three more worked tool-call examples.
  Results (23 gold): qwen2.5:14b answer accuracy **0.30 → 0.65** (tool 0.74,
  args 0.35, mean loop 8.2 s); qwen2.5:7b 0.35 → 0.43 (tool 0.78, TTFT 0.9 s,
  mean 4.0 s). no_tool_on_chitchat stays 1.0. One wobble: 14b fragmented
  recall 0.5 (1 of 2 probes — small-n, watch it). README table updated.
  192 tests green.

- **2026-10-12 — Nemotron-3-Nano-4B audition (cascade, same harness):**
  `hf.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-GGUF:Q4_K_M` — answer accuracy
  **0.83** (best yet; isolate failures 2 retrieval + 2 model), tool 0.78,
  args 0.39, chitchat precision 1.0, fragmented 1.0. Cost: it reasons before
  speaking — loop mean 14.6 s, max 30 s, and the 279 ms "TTFT" is the first
  REASONING chunk, not speech (Ollama puts its thinking in a separate
  `reasoning` field; pipecat speaks only `content`, so nothing leaks but the
  user waits through the think). ~3 GB at Q4 — fits small laptops. Untested
  lever: Nemotron's reasoning toggle (system-prompt switch) for a
  speed/accuracy middle point. Not made default; human decides.

- **2026-10-12 — nemotron alias + pull guard + money rain.**
  `prefetch_models.py` gains OLLAMA_ALIASES (`nemotron-nano:4b` →
  `hf.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-GGUF:Q4_K_M`): pulls the HF source,
  `ollama cp`s the short name (shared blobs). `run_ollama.sh` now skips
  `ollama pull` for tags already in the store — pull dials the registry even
  for present models, which broke offline restarts and local-only aliases.
  Alias created on this machine. UI: `locat-busy` RTVI message pushed from the
  filler handler; `attachMoneyRain` in panels.ts rains 💸🪙💵💰 over the
  retrieved-context panel while tools run (pure CSS, offline, 30 s cap),
  cleared by the route line or the next bot output. dist rebuilt.

- **2026-10-12 — two post-collapse stragglers found by human testing:**
  (1) bot_moq.py kept its own dual `_preflight_llm` call (cleanup grep had been
  head-truncated) — removed; added an entry-point import smoke test so bot.py /
  bot_moq.py breakage can't evade the suite again. (2) Retrieved-context panel
  crashed rendering semantic chunks — panels.ts expected `source_path`/`score`
  but retrieval chunks carry `{source, page, text}` (no per-chunk score since
  RRF) — the TypeError left the "no documents indexed" placeholder up even
  though tools ran fine. Shape fixed, dist rebuilt. 198 tests green.

- **2026-10-12 — garbled greeting root-caused.** The greeting always ducked
  around "offline financial thinking": logs show the server subscribes to the
  PEER's mic track ~1.5 s after greeting synthesis starts ("MOQ: subscribing to
  peer audio"), spinning up Opus decode + VAD + the browser's echo canceller
  mid-sentence. The subscriber gate now also waits on the transport's
  `on_track_subscribed` (inbound mic) plus a 0.5 s settle
  (bot_moq.GREETING_SETTLE_SECS) before speaking. Also this session:
  `reasoning_effort: "none"` wired into build_llm + all eval /v1 bodies —
  native think:false doesn't cross Ollama's /v1, reasoning_effort does
  (verified: nemotron thinks silently for minutes otherwise, and live stalls
  compound with interruptions + untranscribed short utterances). Nemotron
  reasoning-off accuracy NOT yet re-measured. 201 tests green.

- **2026-10-12 — nemotron twins (vp-nemotwins): built + measured.**
  LOCAT_ARCHITECTURE=cascade|split; split = same model tag in two configs:
  pipeline front (/v1, reasoning_effort none, one delegate tool, deterministic
  relay) + rag_agent records analyst (native /api/chat, think on, four tools,
  per-call grounding incl. categories). 217 tests. `--stage split` eval,
  nemotron-3-nano:4b both roles: delegation precision 1.0, fragmented 1.0,
  front decision ~1.0 s — but delegation RECALL 0.74 (6/23 data questions
  answered by the front from memory) and back accuracy 0.70 (mean 11.5 s,
  max 23 s). Composite data-question accuracy ≈ 0.74 × 0.70 ≈ 0.52 — behind
  the cascade-14b baseline (0.65 @ 8.2 s mean). Front recall is the lever if
  the twins get another round. Alias renamed nemotron-nano → nemotron-3-nano:4b
  (human's choice) across env/test/README/ollama store.

- **2026-10-12 — twins round 2 + watchdog + rain fix.** Prompt iteration:
  back +relative-date example → 0.70→0.74; front "always/when-in-doubt" clause
  → recall 0.74→0.70, fragmented 1.0→0.5 — front routing does NOT yield to
  prompting on a no-reasoning 4B. Composite stuck ≈0.51 vs cascade-14b 0.65.
  Next lever (human's idea): observer architecture — records agent taps user
  turns directly, decides relevance itself, injects spoken answer + context
  update; removes delegation recall from the equation (effective ≈ back's
  0.74). Also: SilenceWatchdog processor (30 s dead-air reminder ×3, filler
  bypasses it so hung tool calls are caught) wired between LLM and TTS, 5
  tests; money rain was killed by its own BotOutput stop listener (the filler
  IS bot output) — now stops only on route lines / 30 s cap; cascade tests pin
  LOCAT_ARCHITECTURE (dev .env had split). 223 tests green.

- **2026-10-13 — observer architecture built + measured: new leader.**
  LOCAT_ARCHITECTURE=observer: RecordsObserver (after the user aggregator)
  runs the thinking-on analyst on every user turn in parallel with the front;
  NOT_RECORDS sentinel keeps it silent on chitchat; answers spoken via
  TTSSpeakFrame + per-trace route lines + written into the front's context as
  assistant turns; barge-in/new-turn cancels in-flight lookups. Front prompt
  OBSERVER_FRONT_PROMPT (no tools, defer clause). Eval (--stage observer,
  nemotron-3-nano:4b): **effective answer accuracy 0.70** (no recall
  multiplier), sentinel_on_data 0.04 (1/23 missed), silent_on_chitchat 1.0,
  mean 11.3 s / max 19.6 s. Beats cascade-14b (0.65) from a 2.8 GB model.
  Gap to the 0.83 cascade-thinking ceiling = analyst-prompt work. 228 tests.
  README model table reworked around model+architecture pairs; RAM wording
  honest (8 GB works with smaller STT; 16 GB comfortable).

- **2026-10-13 — "observer is the worst" was actually the split: .env clobbered
  the CLI.** `LOCAT_ARCHITECTURE=observer ./locat.sh start` ran SPLIT because
  locat.sh/run_ollama.sh sourced .env with `set -a`, overwriting command-line
  env vars (.env still said split). Both scripts now use load_env_defaults():
  .env supplies defaults only, real env wins — matching config.py's
  load_dotenv(override=False); regression test added (set -e gotcha: `[[ ]] &&`
  lists fail the function — use if/fi). The accidental split live session is
  damning evidence for its retirement: ZERO structured tool calls — the
  reasoning-off front NARRATED "[Using ask_financial_records]" as speech and
  hallucinated passphrase answers with markdown. Also: observer mode had no
  money-rain trigger — it now pushes locat-busy at lookup start and a new
  locat-idle on sentinel/error/cancel (panel stops on route lines or idle).
  229 tests green. Observer still awaits its first real ears-on run.

- **2026-10-13 — first REAL observer session findings + fixes.** The 22:17 log
  (RecordsObserver linked, zero tools) showed: (1) the reasoning-off front
  HALLUCINATED passphrase answers instead of deferring, and parroted its own
  prompt's "$40.00" example as "forty dollars" ×5 — OBSERVER_FRONT_PROMPT
  rewritten: no parrotable examples, v1-style "you have no access / never
  invent" hardening, no number guidance at all. (2) The analyst found
  passphrase-smoke.txt but WITHHELD the phrase (credential-shy) — analyst
  prompt gains the owner's-data read-back-verbatim clause. (3) Watchdog never
  fired in observer mode: the front's instant chatter disarmed it — now
  busy-aware (locat-busy arms + makes LLMText non-disarming; idle/route/spoken
  answer clear). (4) Architecture now shown in the what's-running panel
  (locat-config "architecture") — ends which-code-is-running guesswork.
  (5) LOCAT_RAG_LLM_MODEL reintroduced as the analyst's model (default:
  llm_model()) → mixed pairs possible, e.g. obedient qwen2.5:7b front +
  thinking nemotron analyst. 232 tests green. Rain was absent in that session
  because busy/idle landed after it — already fixed.

- **2026-10-13 — observer round 2: context blindness fixed; mixed pair is the
  config.** Q3 live failure root-caused: the observer sent the analyst ONLY the
  latest user message — follow-ups ("what were those amounts?") were
  unanswerable; a joke request mid-wait also CANCELS the pending lookup (new
  turn). Observer now sends the last ~6 user/assistant turns with the question
  last; analyst prompt resolves references against them. The "45.99 ×3" answer
  was the no-think nemotron FRONT fabricating (real: 122.09/114.45/65.27,
  1.5 s response = not the analyst) — even the hardened prompt doesn't stop it
  on re-asked follow-ups. Probe: qwen2.5:7b under OBSERVER_FRONT_PROMPT defers
  4/4 (incl. that follow-up), zero digits spoken. Recommended config:
  LOCAT_LLM_MODEL=qwen2.5:7b + LOCAT_RAG_LLM_MODEL=nemotron-3-nano:4b +
  LOCAT_ARCHITECTURE=observer. Watchdog confirmed firing live. 234 tests green.

- **2026-10-13 — LLM arithmetic banned.** Live: analyst listed the three
  correct Whole Foods amounts then mental-mathed the total ($291.81 for a
  $301.81 sum). lookup/filter_rows results now carry `amounts_total` —
  the exact server-computed total of the returned rows — and both prompts say
  never do arithmetic: aggregate for sums/counts, amounts_total when listing.
  The model copies numbers; SQLite adds them. 237 tests green.
