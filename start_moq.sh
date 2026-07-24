#!/usr/bin/env bash
#
# start_moq.sh — run the browser voice bot over MoQ (Media over QUIC): lower latency
# than WebRTC, browser echo cancellation, fully offline. Ensures the repo-local Ollama
# server is up with the LLM pulled, then serves the bot.
#
# Serve mode is the runner default (--moq-serve default True) and it auto-generates a
# localhost TLS cert, so no flags or certs are needed.
#
# Usage:
#   ./start_moq.sh                     # serve (if needed) + run the MoQ bot
#   LLM_MODEL=llama3 ./start_moq.sh    # use a different local model
#
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"
[[ -f .env ]] && { set -a; source .env; set +a; }

OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
BASE="http://${OLLAMA_HOST}"
LLM_MODEL="${LLM_MODEL:-qwen2.5:14b}"
OLLAMA_LOG="$REPO/ollama.log"
WEB_PORT="${WEB_PORT:-7860}"

server_up()   { curl -sf --max-time 2 "$BASE/api/tags" -o /dev/null 2>/dev/null; }
model_ready() { curl -sf --max-time 3 "$BASE/api/tags" 2>/dev/null | grep -q "\"${LLM_MODEL}"; }

if server_up && model_ready; then
  echo "start_moq: Ollama already serving ${LLM_MODEL} at ${BASE}"
else
  echo "start_moq: bringing up repo-local Ollama (store → ./models/ollama; log → ollama.log)"
  nohup ./scripts/run_ollama.sh >"$OLLAMA_LOG" 2>&1 &
  echo "start_moq: waiting for ${LLM_MODEL} (first pull can take a few minutes)..."
  for _ in $(seq 1 900); do model_ready && break; sleep 1; done
  model_ready || { echo "start_moq: ${LLM_MODEL} not ready — see $OLLAMA_LOG" >&2; exit 1; }
fi

echo "start_moq: Ollama ready. Serving the MoQ bot on http://localhost:${WEB_PORT}"
echo "           ▶ Open  http://localhost:${WEB_PORT}  — choose 'Media over QUIC' in the"
echo "             top-left dropdown, allow the mic, and Connect."
echo "           (Ollama keeps running in the background — ./stop.sh to shut it down.)"
# Run the server in its OWN process group (set -m) so Ctrl-C is handled here and
# we can tear down the whole tree (uv + python) cleanly instead of hanging.
set -m
env LLM_MODEL="${LLM_MODEL}" uv run python bot_moq.py --host localhost --port "${WEB_PORT}" &
BOT_PID=$!

stop_bot() {
  trap - INT TERM
  set +e
  echo
  echo "start_moq: stopping server…"
  kill -INT -"$BOT_PID" 2>/dev/null || kill -INT "$BOT_PID" 2>/dev/null
  for _ in $(seq 1 8); do kill -0 "$BOT_PID" 2>/dev/null || break; sleep 0.25; done
  kill -KILL -"$BOT_PID" 2>/dev/null || kill -KILL "$BOT_PID" 2>/dev/null
  exit 0
}
trap stop_bot INT TERM
wait "$BOT_PID"
