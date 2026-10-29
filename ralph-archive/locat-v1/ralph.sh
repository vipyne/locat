#!/usr/bin/env bash
# Ralph loop: feed ralph/PROMPT.md to claude until it prints a promise or MAX_ITER
# is reached. Each iteration is a fresh `claude -p` session; state lives in the repo
# (ralph/PLAN.md checkboxes + Status table, ralph/PROGRESS.md, git history).
#
#   <promise>LOCAT_RAG_COMPLETE</promise>  → everything done                  (exit 0)
#   <promise>HUMAN_GATE</promise>   → loop needs you: see ralph/HUMAN.md (exit 2)
#
#   ./ralph/ralph.sh                 # default: 25 iterations, bypass permission prompts
#   MAX_ITER=5 ./ralph/ralph.sh      # cap
#   PERMS="--permission-mode acceptEdits" ./ralph/ralph.sh   # prompt-free edits, ask for bash
#   MODEL=opus ./ralph/ralph.sh      # force one model for every iteration
#
# Safety: .claude/settings.json has a permissions.deny list that Claude Code
# enforces even under --dangerously-skip-permissions. ralph/PROMPT.md repeats
# those rules. Human-only work is written to ralph/HUMAN.md for you to run.
set -u
cd "$(dirname "$0")/.."   # always run from the repo root
MAX_ITER="${MAX_ITER:-25}"
PERMS="${PERMS:---dangerously-skip-permissions}"
PROMISE="LOCAT_RAG_COMPLETE"
mkdir -p ralph/logs
command -v claude >/dev/null || { echo "claude CLI not found" >&2; exit 1; }
[[ -f ralph/PROMPT.md ]] || { echo "ralph/PROMPT.md missing" >&2; exit 1; }

# next_model: print the Model cell of the first actionable Status-table row in
# ralph/PLAN.md (status pending/in progress), if it is a valid alias; else
# print nothing so claude runs with the session default. Reads the table ONLY
# for model choice — never for stop decisions (those come from the promise).
next_model() {
  awk -F'|' '
    /^\|/ && NF >= 5 {
      status = $4; model = $5
      gsub(/^[ \t]+|[ \t]+$/, "", status)
      gsub(/^[ \t]+|[ \t]+$/, "", model)
      s = tolower(status)
      if (s ~ /^pending/ || s ~ /^in progress/) {
        m = tolower(model)
        if (m == "opus" || m == "sonnet" || m == "haiku") print m
        exit
      }
    }
  ' ralph/PLAN.md 2>/dev/null
}

for ((i = 1; i <= MAX_ITER; i++)); do
  log="ralph/logs/ralph-$(date +%Y%m%d-%H%M%S)-$i.log"
  m="${MODEL:-$(next_model)}"
  MODEL_FLAG=""
  [[ -n "$m" ]] && MODEL_FLAG="--model $m"
  echo "=== ralph iteration $i/$MAX_ITER${m:+ (model: $m)} → $log"
  # shellcheck disable=SC2086
  claude -p "$(cat ralph/PROMPT.md)" $PERMS $MODEL_FLAG --output-format text 2>&1 | tee "$log"
  if grep -q "<promise>$PROMISE</promise>" "$log"; then
    echo "=== promise found after $i iteration(s). Done."
    exit 0
  fi
  if grep -q "<promise>HUMAN_GATE</promise>" "$log"; then
    echo "=== ralph needs you: only gated/blocked work remains." >&2
    echo "=== open ralph/HUMAN.md, do the unchecked items" >&2
    echo "=== (or leave a dated note under a failed gate)," >&2
    echo "=== then rerun ./ralph/ralph.sh" >&2
    exit 2
  fi
  # leave nothing running between iterations
  ./locat.sh stop >/dev/null 2>&1 || true
  sleep 3
done
echo "=== hit MAX_ITER=$MAX_ITER without a promise. See ralph/PROGRESS.md and ralph/logs/." >&2
exit 1
