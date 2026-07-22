#!/usr/bin/env bash
#
# ralph_vpio.sh — autonomous SPIKE loop for macOS VPIO echo cancellation.
#
# Runs Claude Code headless, one small experiment per iteration, until the spike
# reaches a keep/drop DECISION (VPIO_DONE) or needs a human audio test (VPIO_BLOCKED).
# Separate sentinels + progress file from the main ralph.sh so the two never collide.
#
# Usage:
#   ./ralph_vpio.sh                 # run with defaults
#   MAX_ITERS=15 ./ralph_vpio.sh    # cap iterations
#   MODEL=opus ./ralph_vpio.sh      # pin a model
#
# Stop conditions (the agent creates these):
#   VPIO_DONE      -> spike reached a keep/drop decision (see SPIKE_FINDINGS.md)
#   VPIO_BLOCKED   -> needs a human speaker/echo test (see VPIO_BLOCKED.md)
#
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMPT_FILE="${PROMPT_FILE:-$REPO/PROMPT_vpio.md}"
PLAN_FILE="${PLAN_FILE:-$REPO/SPIKE_VPIO.md}"
MAX_ITERS="${MAX_ITERS:-25}"
SLEEP_SECS="${SLEEP_SECS:-3}"
MODEL="${MODEL:-}"
LOG_DIR="$REPO/.ralph/vpio-logs"

cd "$REPO"

command -v claude >/dev/null 2>&1 || { echo "ERROR: 'claude' CLI not found on PATH."; exit 1; }
[ -f "$PROMPT_FILE" ] || { echo "ERROR: prompt file not found: $PROMPT_FILE"; exit 1; }
[ -f "$PLAN_FILE" ]   || { echo "ERROR: spike plan not found: $PLAN_FILE"; exit 1; }

mkdir -p "$LOG_DIR"
[ -d .git ] || { git init -q && echo "→ initialized git repo"; }

echo "→ VPIO spike starting in $REPO (max $MAX_ITERS iterations)"

for ((i=1; i<=MAX_ITERS; i++)); do
  ts="$(date +%Y%m%d-%H%M%S)"
  log="$LOG_DIR/iter-$(printf '%03d' "$i")-$ts.log"
  echo "=== vpio-spike iteration $i/$MAX_ITERS ($ts) ===" | tee -a "$log"

  model_flag=(); [ -n "$MODEL" ] && model_flag=(--model "$MODEL")

  # A spike runs many varied commands (build/test audio probes, web research). We
  # allow a broad-but-scoped toolset. For a freer run, swap the --allowedTools line
  # for: --dangerously-skip-permissions
  cat "$PROMPT_FILE" | claude -p \
      --allowedTools "Edit,Write,WebSearch,WebFetch,Bash(uv:*),Bash(python:*),Bash(python3:*),Bash(git:*),Bash(brew:*),Bash(curl:*),Bash(pip:*),Bash(ls:*),Bash(cat:*),Bash(grep:*),Bash(find:*),mcp__pipecat-context-hub__*" \
      "${model_flag[@]}" \
      2>&1 | tee -a "$log"

  if [ -n "$(git status --porcelain)" ]; then
    git add -A && git commit -q -m "spike(vpio): iteration $i (safety-net commit)" || true
  fi

  if [ -f VPIO_DONE ]; then
    echo "✅ VPIO_DONE after $i iterations — spike reached a decision. See SPIKE_FINDINGS.md."
    exit 0
  fi
  if [ -f VPIO_BLOCKED ]; then
    echo "⏸  VPIO_BLOCKED after $i iterations — needs a human audio test:"
    echo "----------------------------------------------------------------"
    cat VPIO_BLOCKED.md 2>/dev/null
    echo "----------------------------------------------------------------"
    echo "   Do the test, delete VPIO_BLOCKED + VPIO_BLOCKED.md, then re-run ./ralph_vpio.sh"
    exit 2
  fi

  [ "$SLEEP_SECS" -gt 0 ] && sleep "$SLEEP_SECS"
done

echo "⚠️  Hit MAX_ITERS=$MAX_ITERS without a decision. Check $LOG_DIR and SPIKE_PROGRESS.md,"
echo "    then re-run ./ralph_vpio.sh to continue."
exit 3
