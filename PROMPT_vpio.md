# Ralph Loop Task — VPIO Echo-Cancellation Spike

You are **one iteration** of an autonomous **spike** loop. You have **NO memory** of previous
iterations. `SPIKE_VPIO.md`, `SPIKE_PROGRESS.md`, and the git history are your ONLY shared state.
Read them before doing anything.

This is a **spike** (exploratory research), not a build-to-spec task. The goal is a DECISION —
"VPIO works and here's how" or "not worth it, here's why" — backed by evidence. Reaching a
well-evidenced conclusion IS success; you do not have to ship a polished feature.

## Do this, in order
1. **Read `SPIKE_VPIO.md`** (the spike plan) and **`SPIKE_PROGRESS.md`** (progress + findings so
   far). If `SPIKE_PROGRESS.md` doesn't exist, create it from the plan's phases as an unchecked
   checklist, commit, and stop for this iteration.
2. **Pick the FIRST incomplete task** in phase order (0 → 4).
3. **Do ONLY that one task** — the smallest experiment that moves it forward. Prefer a 20-line
   probe over a framework.
4. **Research with real sources.** For macOS audio APIs (AVFoundation `setVoiceProcessingEnabled`,
   `AUVoiceProcessingIO`, CoreAudio) and PyObjC usage, use WebSearch/WebFetch and cite what you
   found in `SPIKE_PROGRESS.md`. For Pipecat transport internals, read the installed source under
   `.venv/lib/python3.12/site-packages/pipecat/transports/` (do not trust memory; the context hub
   lags the installed 1.5.x — see the repo's PROMPT.md note).
5. **Record findings** in `SPIKE_PROGRESS.md`: what you tried, what happened (numbers/errors),
   what you concluded, and what the next step is. This is how the next (amnesiac) iteration
   continues the investigation.
6. **Commit**: `git add -A && git commit -m "spike(vpio): <what you did / found>"`.

## Hard rules
- **Spike discipline:** smallest experiment that answers the question. No polish before AEC is
  proven to work.
- **Keep all prototype code under `spike-vpio/`.** Do NOT modify `bot.py` or the main dependencies
  until Phase 3, and then only behind a non-default `AUDIO_BACKEND=vpio` flag. The working bot must
  keep working.
- **Never fake an audio verification.** Echo reduction and "no self-interruption on speakers"
  require a human on speakers in a quiet room. When the next task needs that, write the exact steps
  and what numbers to report into `VPIO_BLOCKED.md`, create an empty `VPIO_BLOCKED`, commit, and
  stop. Do NOT invent results.
- **Time-box honestly.** If a chosen approach hasn't shown AEC working after ~2 rounds, record a
  "not feasible without excessive effort" finding with evidence and move toward the DROP conclusion
  — that is a valid successful outcome, not a failure.
- **Use `uv` for Python**; keep spike deps out of the main project (spike-local venv or clearly
  optional). Note any `brew`/system deps in `SPIKE_PROGRESS.md`.
- When the spike has reached a clear KEEP or DROP decision and written `SPIKE_FINDINGS.md`, create
  an empty `VPIO_DONE`, commit, and stop.

## Reference
- Spike plan / source of truth: **`SPIKE_VPIO.md`**
- Pipecat transport internals: installed source in `.venv/.../pipecat/transports/`
- macOS audio: Apple developer docs + PyObjC (via WebSearch/WebFetch)
