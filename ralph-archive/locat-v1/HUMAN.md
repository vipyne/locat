# HUMAN.md — things only you can do

Ordered. Each item has the exact command(s). The ralph loop never runs these,
but it reads this file FIRST every iteration.

Conventions:
- **Gate:** items stop the loop. When only gated/blocked work remains, the loop
  prints `<promise>HUMAN_GATE</promise>` and `ralph.sh` exits pointing here.
  Do the item, tick its box, rerun `./ralph/ralph.sh`.
- **Gate failed?** Leave the box UNCHECKED and write a dated note directly under
  the item — what you ran, what you saw, verbatim errors. The next loop run
  treats that note as a bug report and turns it into plan work.

## 1. Review + push
- [x] **Gate: review before anything is pushed.** `git log --oneline` + skim the
      diffs (`git diff <last-commit-you-reviewed>..HEAD`). Push only when happy —
      the loop only commits locally.

## 2. Voice smoke test (Task 5)
Needs your ears and your voice — no command can confirm what came out of the
speakers or how the streaming answer feels.

- [ ] **Gate: single-model cascade works end to end — tools, streaming, canary,
      barge-in, refusal.** (Re-opened 2026-10-12 for the workshop cut: one model,
      qwen2.5:14b, holds the four tools directly; answers stream to the voice.)
      ```bash
      ./locat.sh status          # confirm: LLM qwen2.5:14b, embed model, "index: … chunks"
      ./locat.sh start           # browser UI at http://localhost:7860
      ```
      Speak (or type) each, watching the retrieved-context panel's route lines:
      1. Lookup — "what did I pay Navy Federal?" → a `lookup` route line, answer
         names the merchant and amount.
      2. Filter — "what were my five biggest expenses in March twenty twenty-five?"
         → `filter_rows`, five concrete transactions.
      3. Aggregate — "how much did I spend on groceries in twenty twenty-five?"
         → `aggregate`, a plausible number, spoken as words.
      4. Semantic — "what's the secret passphrase for the tax vault?" →
         `semantic_search`, answers BLUE MANATEE 436921 citing passphrase-smoke.txt.
      5. Relative date — "how much did I spend last August?" → an August of LAST
         YEAR between-filter in the route line, not some other year.
      Also confirm, ears-on:
      - "Let me check your records." plays while tools run — once, no
        double-speak, and the answer starts streaming right after (no long dead
        air waiting for a full composition).
      - Say "good morning" and "what does diversification mean?" — NO route line
        appears, the bot just talks.
      - Interrupt mid-answer while a lookup is running, then ask again — nothing
        stuck, nothing leaked, no half-answer replayed.
      - Ask something NOT in the data ("how much did I spend on yachts?") — it
        says it couldn't find it rather than inventing a number.
      - Each bot line appears ONCE in the transcript (the greeting too).
      Then `./locat.sh stop`.

## 3. Eval results review (Task 7)
The loop ran `evals/run_evals.py` and pasted the report into `ralph/PROGRESS.md`.
Only you can decide whether the numbers are good enough for the demo.

- [x] **Gate: read the eval report and sign off.**
      ```bash
      uv run python evals/run_evals.py     # rerun yourself if you want fresh numbers
      ```
      Judge, per column:
      - Retrieval accuracy vs answer accuracy per query type (the isolate stage
        tells you which side broke — relay failures are the 1b's fault, not
        retrieval's).
      - Delegation recall/precision (1b calling the tool when it should and only
        then) and ack-rate (acknowledgment spoken before the tool call).
      - Relay fidelity (≥0.95 similarity; below that the pre-declared contingency
        is deterministic relay — the handler speaks the answer itself with
        run_llm=False).
      - Latency: 1b ack + backend round-trip ≈ the pause you feel; backend round
        breakdown shows where time goes.
      If something's off, leave a dated note under this item (e.g. "delegation
      recall 60% — sharpen tool description" or "relay fidelity 0.8 — switch to
      deterministic relay" or "aggregate args wrong on 3/5 — flat-params
      contingency") and the loop will turn it into plan work.
