#!/usr/bin/env bash
#
# start.sh — one command to run the fully-offline voice bot.
#
# Ensures the repo-local Ollama server is up (models in ./models/ollama) with the
# LLM pulled, then launches the bot. Ollama is left running in the background so
# subsequent starts are instant; stop it with ./stop.sh.
#
# Usage:
#   ./start.sh                     # serve (if needed) + run the bot
#   LLM_MODEL=llama3 ./start.sh    # use a different local model
#
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"
[[ -f .env ]] && { set -a; source .env; set +a; }

OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
BASE="http://${OLLAMA_HOST}"
LLM_MODEL="${LLM_MODEL:-qwen2.5:14b}"
OLLAMA_LOG="$REPO/ollama.log"

server_up()   { curl -sf --max-time 2 "$BASE/api/tags" -o /dev/null 2>/dev/null; }
model_ready() { curl -sf --max-time 3 "$BASE/api/tags" 2>/dev/null | grep -q "\"${LLM_MODEL}"; }

if server_up && model_ready; then
  echo "start: Ollama already serving ${LLM_MODEL} at ${BASE}"
else
  echo "start: bringing up repo-local Ollama (store → ./models/ollama; log → ollama.log)"
  # run_ollama.sh starts 'ollama serve' (if needed) + pulls the model, then blocks.
  nohup ./scripts/run_ollama.sh >"$OLLAMA_LOG" 2>&1 &
  echo "start: waiting for ${LLM_MODEL} (first pull can take a few minutes)..."
  for _ in $(seq 1 900); do model_ready && break; sleep 1; done
  model_ready || { echo "start: ${LLM_MODEL} not ready — see $OLLAMA_LOG" >&2; exit 1; }
fi

echo "start: Ollama ready. Launching bot (LLM_MODEL=${LLM_MODEL}). Ctrl-C to stop the bot;"
echo "       Ollama keeps running in the background — run ./stop.sh to shut it down."
exec env LLM_MODEL="${LLM_MODEL}" uv run bot.py
