#!/usr/bin/env bash
# Model sweep: run each analyst candidate through --stage analyst_followup and each
# front candidate through --stage front, write JSON reports to evals/results/,
# print a leaderboard. Override candidates via ANALYSTS / FRONTS env vars
# (space-separated Ollama tags).
#
#   ./evals/sweep.sh
#   ANALYSTS="qwen3:8b nemotron-3-nano:4b" FRONTS="qwen2.5:7b" ./evals/sweep.sh
set -euo pipefail
cd "$(dirname "$0")/.."

RESULTS=evals/results
mkdir -p "$RESULTS"

DEFAULT_ANALYSTS="nemotron-3-nano:4b qwen3:4b qwen3:8b llama3-groq-tool-use:8b granite3.3:8b"
DEFAULT_FRONTS="qwen2.5:7b qwen3:4b"
read -r -a analysts <<< "${ANALYSTS:-$DEFAULT_ANALYSTS}"
read -r -a fronts <<< "${FRONTS:-$DEFAULT_FRONTS}"

api="${LOFI_OLLAMA_BASE_URL:-http://localhost:11434/v1}"
api="${api%/v1}"

have() { curl -s "$api/api/tags" | grep -q "\"name\":\"$1\""; }

pull() {
  if ! have "$1"; then
    echo "== pulling $1"
    ollama pull "$1" || { echo "!! could not pull $1 — skipping"; return 1; }
  fi
}

tag() { echo "${1//[:\/]/-}"; }

for m in "${analysts[@]}"; do
  pull "$m" || continue
  echo "== analyst: $m (--stage analyst_followup)"
  LOFI_RAG_LLM_MODEL="$m" uv run python evals/run_evals.py \
    --stage analyst_followup --json "$RESULTS/analyst-$(tag "$m").json"
done

for m in "${fronts[@]}"; do
  pull "$m" || continue
  echo "== front: $m (--stage front)"
  LOFI_LLM_MODEL="$m" uv run python evals/run_evals.py \
    --stage front --json "$RESULTS/front-$(tag "$m").json"
done

echo
uv run python - "$RESULTS" <<'EOF'
import json, sys
from pathlib import Path

results = Path(sys.argv[1])

def fmt(v):
    return f"{v:.2f}" if isinstance(v, (int, float)) else "-"

rows = []
for f in sorted(results.glob("analyst-*.json")):
    r = json.loads(f.read_text())["reports"].get("analyst_followup") or {}
    meta = json.loads(f.read_text())["meta"]
    rows.append((meta["rag_llm_model"], r.get("analyst_retrieval_rate"),
                 r.get("analyst_no_invented_fact_rate"), r.get("errors"),
                 r.get("analyst_ms_avg")))
if rows:
    print("== analyst leaderboard (analyst_followup stage) ==")
    print(f"{'model':30} {'retrieves':>9} {'no_invent':>9} {'errors':>7} {'ms_avg':>9}")
    for m, a, n, e, ms in sorted(rows, key=lambda r: (-(r[1] or 0), -(r[2] or 0))):
        print(f"{m:30} {fmt(a):>9} {fmt(n):>9} {e if e is not None else '-':>7} {ms or '-':>9}")

rows = []
for f in sorted(results.glob("front-*.json")):
    payload = json.loads(f.read_text())
    r = payload["reports"].get("front") or {}
    rows.append((payload["meta"]["llm_model"], r.get("no_invented_numbers_rate"),
                 r.get("defers_on_data"), r.get("answers_chitchat"),
                 r.get("errors"), r.get("front_ms_avg")))
if rows:
    print("\n== front leaderboard (front stage) ==")
    print(f"{'model':30} {'no_invent':>9} {'defers':>8} {'chit_ok':>8} {'errors':>7} {'ms_avg':>8}")
    for m, n, d, c, e, ms in sorted(rows, key=lambda r: (-(r[1] or 0), -(r[2] or 0))):
        print(f"{m:30} {fmt(n):>9} {fmt(d):>8} {fmt(c):>8} {e if e is not None else '-':>7} {ms or '-':>8}")
EOF
