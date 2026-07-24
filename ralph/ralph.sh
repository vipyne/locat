#!/usr/bin/env bash
#
# ralph.sh — autonomous build loop for the offline Pipecat voice bot.
#
# Runs Claude Code headless, repeatedly, one small task per iteration, until the
# work is DONE or BLOCKED on a human. Each iteration is a fresh agent with no
# memory: the repo files + git history + PLAN.md + PROGRESS.md are the only
# shared state (that's why PROMPT.md forces "one task, write it down, commit").
#
# Run from the repo root (the script finds its own dir):
#   ./ralph/ralph.sh                # run with defaults
#   MAX_ITERS=20 ./ralph/ralph.sh   # cap iterations
#   MODEL=opus ./ralph/ralph.sh     # pin a model
#   SLEEP_SECS=0 ./ralph/ralph.sh   # no pause between iterations
#
# Stop conditions (the agent creates these in ralph/; the loop watches for them):
#   ralph/RALPH_DONE      -> all non-human phases complete & verified
#   ralph/RALPH_BLOCKED   -> needs a human (see ralph/RALPH_BLOCKED.md)
#
set -uo pipefail

# --- config (override via env) ---------------------------------------------
# This script lives in ralph/ but operates on the REPO ROOT (its parent), where
# the actual project (bot.py, etc.) is built. All loop state — prompt, plan,
# logs, PROGRESS, sentinels — lives here in ralph/.
RALPH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$RALPH_DIR/.." && pwd)}"
PLAN_SRC="${PLAN_SRC:-$HOME/.claude/plans/agile-purring-kitten.md}"
PROMPT_FILE="${PROMPT_FILE:-$RALPH_DIR/PROMPT.md}"
MAX_ITERS="${MAX_ITERS:-25}"
SLEEP_SECS="${SLEEP_SECS:-3}"
MODEL="${MODEL:-}"                 # e.g. "opus" / "sonnet"; empty = CLI default
LOG_DIR="$RALPH_DIR/logs"

cd "$REPO_DIR"

# --- preflight --------------------------------------------------------------
command -v claude >/dev/null 2>&1 || { echo "ERROR: 'claude' CLI not found on PATH."; exit 1; }
[ -f "$PROMPT_FILE" ] || { echo "ERROR: prompt file not found: $PROMPT_FILE"; exit 1; }

# --- bootstrap --------------------------------------------------------------
mkdir -p "$LOG_DIR"
[ -d .git ] || { git init -q && echo "→ initialized git repo"; }

# Bring the approved plan into ralph/ (self-contained + version-controlled).
if [ ! -f "$RALPH_DIR/PLAN.md" ]; then
  if [ -f "$PLAN_SRC" ]; then
    cp "$PLAN_SRC" "$RALPH_DIR/PLAN.md"
    git add "$RALPH_DIR/PLAN.md" && git commit -q -m "ralph: import approved plan as ralph/PLAN.md" || true
    echo "→ imported plan: $PLAN_SRC -> ralph/PLAN.md"
  else
    echo "ERROR: no ralph/PLAN.md and PLAN_SRC not found: $PLAN_SRC"; exit 1
  fi
fi

echo "→ ralph starting in $REPO_DIR (max $MAX_ITERS iterations)"
echo "→ MCP servers visible to the agent:"; claude mcp list 2>/dev/null | sed 's/^/    /' || true

# --- loop -------------------------------------------------------------------
for ((i=1; i<=MAX_ITERS; i++)); do
  ts="$(date +%Y%m%d-%H%M%S)"
  log="$LOG_DIR/iter-$(printf '%03d' "$i")-$ts.log"
  echo "=== ralph iteration $i/$MAX_ITERS ($ts) ===" | tee -a "$log"

  model_flag=(); [ -n "$MODEL" ] && model_flag=(--model "$MODEL")

  # Fresh headless agent. Prompt via stdin. Shared state = files + git only.
  cat "$PROMPT_FILE" | claude -p \
      --allowedTools "Edit,Write,Bash(uv:*),Bash(git:*),Bash(brew:*),mcp__pipecat-context-hub__*" \
      "${model_flag[@]}" \
      2>&1 | tee -a "$log"
      # --dangerously-skip-permissions \

  # Safety-net commit in case the agent forgot to commit its own work.
  if [ -n "$(git status --porcelain)" ]; then
    git add -A && git commit -q -m "ralph: iteration $i (safety-net commit)" || true
  fi

  if [ -f "$RALPH_DIR/RALPH_DONE" ]; then
    echo "✅ RALPH_DONE after $i iterations — all autonomous phases complete."
    echo "   Remaining human steps are noted in ralph/PROGRESS.md."
    exit 0
  fi
  if [ -f "$RALPH_DIR/RALPH_BLOCKED" ]; then
    echo "⏸  RALPH_BLOCKED after $i iterations — needs a human:"
    echo "----------------------------------------------------------------"
    cat "$RALPH_DIR/RALPH_BLOCKED.md" 2>/dev/null
    echo "----------------------------------------------------------------"
    echo "   Resolve the above, delete ralph/RALPH_BLOCKED, then re-run ./ralph/ralph.sh"
    exit 2
  fi

  [ "$SLEEP_SECS" -gt 0 ] && sleep "$SLEEP_SECS"
done

echo "⚠️  Hit MAX_ITERS=$MAX_ITERS without finishing. Inspect $LOG_DIR and ralph/PROGRESS.md,"
echo "    then re-run ./ralph/ralph.sh to continue (it resumes from ralph/PROGRESS.md)."
exit 3
