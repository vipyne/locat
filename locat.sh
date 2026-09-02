#!/usr/bin/env bash
#
# locat.sh — the one lifecycle command for the fully-offline voice bot.
#
# Ownership rule: locat only ever stops PIDs it recorded in $LOCAT_STATE_DIR
# (default .locat/). An Ollama you started yourself is never touched.
#
# Usage:
#   ./locat.sh start [-t moq|headphones]   # bring up Ollama if needed, then the bot (default: moq)
#   ./locat.sh stop                        # stop only what locat started (recorded PIDs)
#   ./locat.sh status                      # ownership, bot, models, rag index
#   ./locat.sh index-rag                   # build/rebuild the document index
#   ./locat.sh configure [args]            # delegates to ./configure.sh
#   ./locat.sh models [args]               # delegates to scripts/print_models.py
#
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"
[[ -f .env ]] && { set -a; source .env; set +a; }
LOCAT_REPO_ROOT="$REPO"
# shellcheck source=scripts/model_dir.sh
. "$REPO/scripts/model_dir.sh"

STATE_DIR="${LOCAT_STATE_DIR:-.locat}"
case "$STATE_DIR" in /*) ;; *) STATE_DIR="$REPO/$STATE_DIR" ;; esac

OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
OLLAMA_BASE="http://${OLLAMA_HOST}"
LOCAT_LLM_MODEL="${LOCAT_LLM_MODEL:-qwen2.5:14b}"
LOCAT_WEB_PORT="${LOCAT_WEB_PORT:-7860}"
WEB_URL="http://localhost:${LOCAT_WEB_PORT}"

usage() { grep '^#   ' "$0" | sed 's/^#   //'; }

ollama_up()   { curl -sf --max-time 2 "$OLLAMA_BASE/api/tags" -o /dev/null 2>/dev/null; }
model_ready() { curl -sf --max-time 3 "$OLLAMA_BASE/api/tags" 2>/dev/null | grep -q "\"${LOCAT_LLM_MODEL}"; }

pid_alive()    { [[ -n "${1:-}" ]] && ps -p "$1" >/dev/null 2>&1; }
recorded_pid() { cat "$STATE_DIR/$1.pid" 2>/dev/null || true; }

with_descendants() {
  local child
  for child in $(pgrep -P "$1" 2>/dev/null); do with_descendants "$child"; done
  echo "$1"
}

stop_tree() {
  local pids
  pids="$(with_descendants "$1")"
  # shellcheck disable=SC2086
  kill -TERM $pids 2>/dev/null || true
  for _ in $(seq 1 20); do pid_alive "$1" || break; sleep 0.25; done
  # shellcheck disable=SC2086
  pid_alive "$1" && kill -KILL $pids 2>/dev/null || true
}

model_lines() {
  uv run python scripts/print_models.py "$@" 2>/dev/null \
    || echo "         (could not resolve models — run 'uv sync' and retry)"
}

rag_line() {
  uv run python rag.py stats --bare 2>/dev/null \
    || echo "no index (run ./locat.sh index-rag)"
}

cmd_start() {
  local transport="moq"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -t|--transport) transport="${2:-}"; shift 2 || shift ;;
      -h|--help)      usage; exit 0 ;;
      *) echo "locat: unknown start option '$1' (try ./locat.sh -h)" >&2; exit 1 ;;
    esac
  done
  case "$transport" in
    moq|headphones) ;;
    *) echo "locat: unknown transport '$transport' — valid: moq (default), headphones" >&2; exit 1 ;;
  esac

  mkdir -p "$STATE_DIR"

  local bot_pid
  bot_pid="$(recorded_pid bot)"
  if pid_alive "$bot_pid"; then
    echo "start: bot already running (pid $bot_pid) — run ./locat.sh stop first." >&2
    exit 1
  fi

  if ! ollama_up; then
    echo "start: bringing up Ollama (store → ${OLLAMA_MODELS}; log → ollama.log)"
    nohup ./scripts/run_ollama.sh >"$REPO/ollama.log" 2>&1 &
    echo $! >"$STATE_DIR/ollama.pid"
  elif ! model_ready; then
    echo "start: Ollama already running — pulling ${LOCAT_LLM_MODEL} into it..."
    ./scripts/run_ollama.sh
  fi
  if ! model_ready; then
    echo "start: waiting for ${LOCAT_LLM_MODEL} (first pull can take a few minutes)..."
    for _ in $(seq 1 900); do model_ready && break; sleep 1; done
    model_ready || { echo "start: ${LOCAT_LLM_MODEL} not ready — see ollama.log" >&2; exit 1; }
  fi

  model_lines

  local bot_log="$STATE_DIR/bot.log"
  case "$transport" in
    moq)
      nohup uv run python bot_moq.py --host localhost --port "$LOCAT_WEB_PORT" >"$bot_log" 2>&1 &
      ;;
    headphones)
      echo "start: launching the local-audio bot — use headphones 🎧"
      nohup uv run python bot.py >"$bot_log" 2>&1 &
      ;;
  esac
  echo $! >"$STATE_DIR/bot.pid"
  echo "$transport" >"$STATE_DIR/bot.transport"
  echo "start: bot starting (pid $(recorded_pid bot), log → ${bot_log#"$REPO"/})"
  echo "start: stop everything locat started with ./locat.sh stop"

  if [[ "$transport" == "moq" ]]; then
    for _ in $(seq 1 120); do
      curl -sf --max-time 2 "$WEB_URL/" -o /dev/null 2>/dev/null && break
      pid_alive "$(recorded_pid bot)" || { echo "start: bot exited early — see ${bot_log#"$REPO"/}" >&2; exit 1; }
      sleep 1
    done
    echo "start: ▶ $WEB_URL — allow the mic and Connect."
    if command -v open >/dev/null 2>&1; then
      open "$WEB_URL"
    elif command -v xdg-open >/dev/null 2>&1; then
      xdg-open "$WEB_URL"
    fi
  fi
}

cmd_stop() {
  local stopped=0 pidfile name pid cmd
  for pidfile in "$STATE_DIR"/*.pid; do
    [[ -e "$pidfile" ]] || continue
    name="$(basename "$pidfile" .pid)"
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if ! pid_alive "$pid"; then
      echo "stop: $name pidfile is stale (pid ${pid:-?} not running) — removing it."
      rm -f "$pidfile"
    else
      cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
      if ! echo "$cmd" | grep -qE 'run_ollama|ollama|bot|python|uv|sleep'; then
        echo "stop: pid $pid in $name.pid now runs '$cmd' — not locat's, removing the pidfile only."
        rm -f "$pidfile"
      else
        echo "stop: stopping $name (pid $pid)."
        stop_tree "$pid"
        rm -f "$pidfile"
        stopped=1
      fi
    fi
    [[ "$name" == "bot" ]] && rm -f "$STATE_DIR/bot.transport"
  done
  if ollama_up && [[ ! -f "$STATE_DIR/ollama.pid" ]]; then
    echo "ollama: running, NOT started by locat — leaving it alone."
  fi
  [[ "$stopped" -eq 0 ]] && echo "stop: nothing locat started is running."
  return 0
}

cmd_status() {
  local pid transport
  pid="$(recorded_pid ollama)"
  if ollama_up; then
    if pid_alive "$pid"; then
      echo "ollama   running (pid $pid), started by locat, store: ${OLLAMA_MODELS}"
    else
      echo "ollama   running, NOT started by locat (your own instance; ./locat.sh stop leaves it alone)"
    fi
  else
    echo "ollama   not running (./locat.sh start brings it up)"
  fi

  pid="$(recorded_pid bot)"
  if pid_alive "$pid"; then
    transport="$(cat "$STATE_DIR/bot.transport" 2>/dev/null || echo "?")"
    if [[ "$transport" == "moq" ]]; then
      echo "bot      running (pid $pid), transport: moq, $WEB_URL"
    else
      echo "bot      running (pid $pid), transport: $transport"
    fi
  else
    echo "bot      not running (./locat.sh start)"
  fi

  model_lines --bare
  echo "rag      $(rag_line)"
}

CMD="${1:-}"
[[ $# -gt 0 ]] && shift
case "$CMD" in
  start)      cmd_start "$@" ;;
  stop)       cmd_stop ;;
  status)     cmd_status ;;
  index-rag)  exec uv run python rag.py index ;;
  configure)  exec ./configure.sh "$@" ;;
  models)     exec uv run python scripts/print_models.py "$@" ;;
  -h|--help|help|"") usage ;;
  *) echo "locat: unknown command '$CMD' (try ./locat.sh -h)" >&2; exit 1 ;;
esac
