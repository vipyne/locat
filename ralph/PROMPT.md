# Ralph Loop Task — Offline Pipecat Voice Bot

You are **one iteration** of an autonomous build loop. You have **NO memory** of previous
iterations. The repository files, git history, `ralph/PLAN.md`, and `ralph/PROGRESS.md` are your ONLY shared
state. Read them before doing anything.

## Do this, in order

1. **Read `ralph/PLAN.md`** (the full approved spec) and **`ralph/PROGRESS.md`** (what's done so far).
   - If `ralph/PROGRESS.md` does not exist, create it: list every phase and its tasks from `ralph/PLAN.md` as
     an unchecked `- [ ]` checklist, then commit it, and stop for this iteration.
2. **Pick the FIRST incomplete task** in phase order (Phase 0 → 6).
3. **Do ONLY that one task** — or the smallest coherent unit of it. Small, focused changes are the
   point; the next iteration continues. Do NOT try to finish multiple phases at once.
4. **Never guess Pipecat APIs.** For anything under the plan's "confirm at implementation" list
   (extra names, context-aggregator API, `MLXModel` enum members, Smart Turn args, Kokoro voice),
   use the `pipecat-context-hub` MCP tools (`search_api`, `search_docs`, `search_examples`,
   `get_example`, `check_deprecation`) to get ground truth first.
   - **The context-hub index LAGS the installed Pipecat version.** (Installed is 1.5.0; the hub
     indexed ~1.0-era and still presents `PipelineTask`/`PipelineRunner` as current — they are
     deprecated since 1.3.0 in favor of `PipelineWorker`/`WorkerRunner`.) So: get the *concept*
     from the hub, but **verify exact class/param names against the installed source** in
     `.venv/lib/python3.12/site-packages/pipecat/` (grep it). **When the hub and the installed
     package disagree, the installed package wins**, and always prefer the non-deprecated symbol
     (check for `.. deprecated::` / `DeprecationWarning` in the source before using something).
   - After writing code that runs, **treat any `DeprecationWarning` in the output as a task**:
     grep the installed source for the replacement and switch to it before checking the phase off.
5. **Run that task's `Verify` step** from `ralph/PLAN.md`. If it fails, fix it this iteration. Do not
   check the task off until its verification actually passes.
6. **Update `ralph/PROGRESS.md`**: check off what you completed and add a one-line note — what you did,
   any decision you made, any surprise. This is how the next (amnesiac) iteration knows the state.
7. **Commit**: `git add -A && git commit -m "ralph: <phase> — <what you did>"`.

## Hard rules

- **One task per iteration.** Resist finishing everything.
- **Never fake a verification.** If a step cannot be verified programmatically, say so explicitly
  in `ralph/PROGRESS.md` rather than checking it off.
- **Human-gated steps — stop, don't fake:** Some steps require a human and CANNOT be done
  headless:
  - **Phase 6** (offline + spoken conversation test): needs a person speaking into a mic and
    toggling Wi-Fi / Airplane Mode. Phase 5 (README) is the last step you CAN do headless — do it,
    then treat Phase 6 as the blocking hand-off.
  - Any step needing a physical audio device check or manual listening.
  When the next incomplete task is human-gated (or otherwise genuinely blocked — e.g. a model
  download needs network that's unavailable), write exactly what the human must do into
  `ralph/RALPH_BLOCKED.md`, create an empty file named `ralph/RALPH_BLOCKED`, commit, and stop. Do NOT
  pretend it passed.
- **Keep the repo runnable** — never commit a knowingly broken state.
- **Use `uv` for all Python** (`uv sync`, `uv run …`, `uv add …`) — never bare `pip`/`venv`.
- When **ALL non-human-gated phases are complete and verified**, create an empty file named
  `ralph/RALPH_DONE`, note the remaining human steps in `ralph/PROGRESS.md`, commit, and stop.

## Reference
- Spec / task source of truth: **`ralph/PLAN.md`**
- Pipecat docs & API: **`pipecat-context-hub`** MCP server
