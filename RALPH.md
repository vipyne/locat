# RALPH.md — running the autonomous build loop

This repo is built by a **ralph loop**: `ralph.sh` runs a fresh headless Claude Code agent over
and over, one small task per iteration, until the bot is built. This file is the operator runbook.

## What the pieces are
| File | Role |
|---|---|
| `ralph.sh` | The loop. Bootstraps git + `PLAN.md`, runs the agent, watches for stop sentinels. |
| `PROMPT.md` | Fed to the agent each iteration. "Do the first incomplete task, verify, commit." |
| `PLAN.md` | The approved spec / task source of truth (auto-imported from the plan file). |
| `PROGRESS.md` | The agent's checklist + notes. **The shared memory between iterations.** |
| `.ralph/logs/` | One log per iteration. |

Each iteration is **amnesiac** — the only state carried forward is the files + git history. That's
why `PROGRESS.md` matters: it's how iteration N tells iteration N+1 what's done.

## Before the first run
1. **Confirm the CLI and MCP are visible:**
   ```bash
   claude mcp list        # pipecat-context-hub MUST appear, or the agent will guess Pipecat APIs
   ```
2. **Be online.** Phase 2 downloads models (Qwen ~9 GB via Ollama, plus Whisper/Kokoro/VAD/Smart-Turn
   from HuggingFace). The loop needs network until prefetch is done.
3. `brew install portaudio` is needed for PyAudio; the agent should handle it, but it may prompt for
   your macOS password on the first `brew` call — have it handy or run it yourself first.

## Run it
```bash
chmod +x ralph.sh
./ralph.sh
```
Useful overrides:
```bash
MAX_ITERS=15 ./ralph.sh     # cap iterations
MODEL=opus  ./ralph.sh      # pin a model
SLEEP_SECS=0 ./ralph.sh     # no pause between iterations
```

## How it ends
The loop stops when the agent drops a sentinel file:

- **`RALPH_DONE`** → Phases 0–5 are complete & verified. Only the human test (Phase 6) remains.
- **`RALPH_BLOCKED`** → the agent hit something only you can do. Read **`RALPH_BLOCKED.md`** for the
  exact steps, do them, then:
  ```bash
  rm RALPH_BLOCKED RALPH_BLOCKED.md
  ./ralph.sh                # resumes from PROGRESS.md
  ```
- **Hit `MAX_ITERS`** → no sentinel yet. Skim `.ralph/logs/` + `PROGRESS.md`, then re-run to continue.

## The last step is yours (Phase 6)
The loop deliberately will **not** run the offline conversation test — it needs a human speaking
into a mic and toggling Wi-Fi. When you see `RALPH_DONE` (or a Phase-6 `RALPH_BLOCKED`):

```bash
# 1. Start the local LLM server (models stored inside the repo)
./scripts/run_ollama.sh

# 2. Online smoke test — talk to it, confirm you can interrupt it
uv run bot.py

# 3. THE REAL TEST: turn off Wi-Fi / enable Airplane Mode, then:
uv run bot.py
#    Hold a full spoken conversation with no network. That passing = v1 done.
```

## Watching / debugging while it runs
```bash
tail -f .ralph/logs/iter-*.log     # live agent output for the current iteration
git log --oneline                   # what each iteration committed
cat PROGRESS.md                     # current checklist state
```

## If it goes sideways
- **Agent guessing Pipecat APIs / wrong imports** → `pipecat-context-hub` isn't reaching the headless
  agent. Fix `claude mcp list`, then re-run.
- **Repeatedly failing the same task** → open the latest log, do that one task by hand, tick it in
  `PROGRESS.md`, commit, and re-run so the loop moves on.
- **Want a tighter leash** → edit `ralph.sh` and replace `--dangerously-skip-permissions` with an
  explicit `--allowedTools "Edit,Write,Bash(uv:*),Bash(git:*),Bash(brew:*),mcp__pipecat-context-hub__*"`.
- **Start over** → `git reset --hard <commit>` to a known-good iteration; the loop resumes from there.
