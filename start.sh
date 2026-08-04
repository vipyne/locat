#!/usr/bin/env bash
#
# start.sh — one command to run the fully-offline voice bot on your transport
# of choice. Ensures the repo-local Ollama server is up (models in
# ./models/ollama) with the LLM pulled, prints the exact models about to be
# used, then launches the bot. Ollama is left running in the background so
# subsequent starts are instant; stop it with ./stop.sh.
#
# Usage:
#   ./start.sh                     # SmallWebRTC transport (default) — browser
#   ./start.sh -t moq              # MoQ transport — browser, lowest latency
#   ./start.sh -t headphones       # local audio hardware (use headphones! 🎧)
#   ./start.sh -h                  # show this help
#   LLM_MODEL=llama3 ./start.sh    # use a different local model
#
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"
[[ -f .env ]] && { set -a; source .env; set +a; }

usage() { grep '^#   ' "$0" | sed 's/^#   //'; }

TRANSPORT="webrtc"
while [[ $# -gt 0 ]]; do
  case "$1" in
    -t|--transport) TRANSPORT="${2:-}"; shift 2 || shift ;;
    -h|--help)      usage; exit 0 ;;
    *) echo "start: unknown option '$1' (try ./start.sh -h)" >&2; exit 1 ;;
  esac
done

case "$TRANSPORT" in
  moq|webrtc|headphones) ;;
  *) echo "start: unknown transport '$TRANSPORT' — valid: webrtc (default), moq, headphones" >&2; exit 1 ;;
esac

OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
BASE="http://${OLLAMA_HOST}"
LLM_MODEL="${LLM_MODEL:-qwen2.5:14b}"
OLLAMA_LOG="$REPO/ollama.log"
WEB_PORT="${WEB_PORT:-7860}"

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

uv run python scripts/print_models.py \
  || echo "start: (could not resolve models — run 'uv sync' and retry)"

case "$TRANSPORT" in
  moq)
    CMD=(uv run python bot_moq.py --host localhost --port "${WEB_PORT}")
    echo "start: Ollama ready."
    echo "       ▶ Open  http://localhost:${WEB_PORT}  — choose 'Media over QUIC' in the"
    echo "         top-left dropdown, allow the mic, and Connect."
    ;;
  webrtc)
    CMD=(uv run python bot_web.py --host localhost --port "${WEB_PORT}")
    echo "start: Ollama ready."
    ;;
  headphones)
    CMD=(uv run bot.py)
    echo "start: Ollama ready. Launching the local-audio bot — use headphones 🎧"
    ;;
esac
echo ""
echo ""
echo " → → → Ctrl-C stops the bot; Ollama keeps running — run ./stop.sh to shut it down."
echo ""
echo ""

# Run the bot in its OWN process group (set -m) so Ctrl-C is handled here in the
# launcher, not swallowed by the bot. We then tear down the whole bot tree
# (uv + python + audio threads): a plain `exec`/foreground bot can hang on audio
# teardown and leave Ctrl-C looking dead.
set -m
env LLM_MODEL="${LLM_MODEL}" "${CMD[@]}" &
BOT_PID=$!

stop_bot() {
  trap - INT TERM
  set +e
  echo
  echo "start: stopping bot…"
  kill -INT -"$BOT_PID" 2>/dev/null || kill -INT "$BOT_PID" 2>/dev/null   # try graceful
  for _ in $(seq 1 8); do kill -0 "$BOT_PID" 2>/dev/null || break; sleep 0.25; done
  kill -KILL -"$BOT_PID" 2>/dev/null || kill -KILL "$BOT_PID" 2>/dev/null  # then force
  exit 0
}
trap stop_bot INT TERM
wait "$BOT_PID"
