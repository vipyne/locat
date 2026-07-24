# RALPH.md — running the autonomous build loop

This repo was built by a **ralph loop**: `ralph/ralph.sh` runs a fresh headless Claude Code agent
over and over, one small task per iteration, until the bot is built. This file is the operator
runbook. Everything the loop needs lives in `ralph/`; the loop operates on the repo root (where
`bot.py` etc. are built).

## What the pieces are
| File | Role |
|---|---|
| `ralph/ralph.sh` | The loop. Bootstraps git + `ralph/PLAN.md`, runs the agent, watches for stop sentinels. |
| `ralph/PROMPT.md` | Fed to the agent each iteration. "Do the first incomplete task, verify, commit." |
| `ralph/PLAN.md` | The approved spec / task source of truth (auto-imported from the plan file). |
| `ralph/PROGRESS.md` | The agent's checklist + notes. **The shared memory between iterations.** (gitignored) |
| `ralph/logs/` | One log per iteration. (gitignored) |

Each iteration is **amnesiac** — the only state carried forward is the files + git history. That's
why `ralph/PROGRESS.md` matters: it's how iteration N tells iteration N+1 what's done.

## Before the first run
1. **Confirm the CLI and MCP are visible:**
   ```bash
   claude mcp list        # pipecat-context-hub MUST appear, or the agent will guess Pipecat APIs
   ```
2. **Be online.** Phase 2 downloads models (Qwen ~9 GB via Ollama, plus Whisper/Kokoro/VAD/Smart-Turn
   from HuggingFace). The loop needs network until prefetch is done.
3. `brew install portaudio` is needed for PyAudio; the agent should handle it, but it may prompt for
   your macOS password on the first `brew` call — have it handy or run it yourself first.

## Run it (from the repo root)
```bash
chmod +x ralph/ralph.sh
./ralph/ralph.sh
```
Useful overrides:
```bash
MAX_ITERS=15 ./ralph/ralph.sh     # cap iterations
MODEL=opus  ./ralph/ralph.sh      # pin a model
SLEEP_SECS=0 ./ralph/ralph.sh     # no pause between iterations
```

## How it ends
The loop stops when the agent drops a sentinel file in `ralph/`:

- **`ralph/RALPH_DONE`** → Phases 0–5 are complete & verified. Only the human test (Phase 6) remains.
- **`ralph/RALPH_BLOCKED`** → the agent hit something only you can do. Read **`ralph/RALPH_BLOCKED.md`**
  for the exact steps, do them, then:
  ```bash
  rm ralph/RALPH_BLOCKED ralph/RALPH_BLOCKED.md
  ./ralph/ralph.sh                # resumes from ralph/PROGRESS.md
  ```
- **Hit `MAX_ITERS`** → no sentinel yet. Skim `ralph/logs/` + `ralph/PROGRESS.md`, then re-run to continue.

## The last step is yours (Phase 6)
The loop deliberately will **not** run the offline conversation test — it needs a human speaking
into a mic and toggling Wi-Fi. When you see `ralph/RALPH_DONE` (or a Phase-6 `ralph/RALPH_BLOCKED`):

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
tail -f ralph/logs/iter-*.log     # live agent output for the current iteration
git log --oneline                  # what each iteration committed
cat ralph/PROGRESS.md              # current checklist state
```

## If it goes sideways
- **Agent guessing Pipecat APIs / wrong imports** → `pipecat-context-hub` isn't reaching the headless
  agent. Fix `claude mcp list`, then re-run.
- **Repeatedly failing the same task** → open the latest log, do that one task by hand, tick it in
  `ralph/PROGRESS.md`, commit, and re-run so the loop moves on.
- **Want a freer leash** → `ralph/ralph.sh` runs the agent with a scoped `--allowedTools` list; for a
  no-prompts run, swap that line for `--dangerously-skip-permissions` (it's already there, commented).
- **Start over** → `git reset --hard <commit>` to a known-good iteration; the loop resumes from there.
