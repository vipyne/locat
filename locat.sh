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
#   ./locat.sh status [-v]                 # machine, ownership, bot, models (configured + downloaded), rag; -v adds full catalogs
#   ./locat.sh index-rag                   # build/rebuild the document index
#   ./locat.sh consolidate [-n]            # adopt models from default HF/Ollama dirs (symlinks)
#   ./locat.sh configure [args]            # delegates to ./configure.sh
#   ./locat.sh get-debug-log [n]           # bundle the latest (or nth-latest) session log for debugging
#   ./locat.sh <command> help              # help for one command
#
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"
ENV_FILE="${LOCAT_ENV_FILE:-.env}"
[[ -f "$ENV_FILE" ]] && { set -a; source "$ENV_FILE"; set +a; }
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

  mkdir -p "$STATE_DIR/logs"
  if [[ -f "$STATE_DIR/bot.log" && ! -L "$STATE_DIR/bot.log" ]]; then
    mv "$STATE_DIR/bot.log" "$STATE_DIR/logs/bot-00000000-pre-upgrade.log"
  fi
  local bot_log="$STATE_DIR/logs/bot-$(date +%Y%m%d-%H%M%S).log"
  ln -sf "$bot_log" "$STATE_DIR/bot.log"
  session_logs | tail -n +11 | xargs rm -f
  case "$transport" in
    moq)
      nohup uv run python bot_moq.py -t moq --host localhost --port "$LOCAT_WEB_PORT" >"$bot_log" 2>&1 &
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

session_logs() {  # newest first; names are timestamped so lexical order is chronological
  { ls -1 "$STATE_DIR/logs"/bot-*.log 2>/dev/null || true; } | sort -r
}

cmd_get_debug_log() {
  local back="${1:-1}" session bundle
  if ! [[ "$back" =~ ^[1-9][0-9]*$ ]]; then
    echo "get-debug-log: expected how many sessions back (a number ≥1), got '$back'" >&2
    exit 1
  fi
  session="$(session_logs | sed -n "${back}p")"
  if [[ -z "$session" ]]; then
    echo "get-debug-log: no session logs in $STATE_DIR/logs — start the bot at least once (./locat.sh start)" >&2
    exit 1
  fi
  bundle="/tmp/locat-debug-$(date +%Y%m%d-%H%M%S).log"
  {
    echo "=== locat debug bundle · $(date) ==="
    echo "=== session log: $session ==="
    echo
    echo "--- locat status at collection time ---"
    cmd_status
    echo
    echo "--- session log ---"
    cat "$session"
    echo
    echo "--- ollama.log (last 200 lines) ---"
    tail -n 200 "$REPO/ollama.log" 2>/dev/null || echo "(no ollama.log)"
  } >"$bundle"
  echo "session: $session"
  echo "debug bundle: $bundle"
}

cmd_help() {
  case "$1" in
    start)
      echo "usage: ./locat.sh start [-t moq|headphones]"
      echo "  -t, --transport   moq (browser over Media-over-QUIC, the default)"
      echo "                    headphones (local mic/speakers via PyAudio — wear headphones)"
      echo "  brings up Ollama first if nothing answers on \$OLLAMA_HOST (and records its pid)" ;;
    stop)
      echo "usage: ./locat.sh stop"
      echo "  stops ONLY processes locat started (pids recorded in $STATE_DIR)"
      echo "  an Ollama you started yourself is reported and left alone" ;;
    status)
      echo "usage: ./locat.sh status [-v]"
      echo "  machine hardware, ollama (ownership + store), bot, configured models (full paths),"
      echo "  rag index, and an inventory of every model on disk with sizes"
      echo "  -v, --verbose   also print the full STT/LLM/TTS catalogs (every model, even ones"
      echo "                  too big for this machine)" ;;
    index-rag)
      echo "usage: ./locat.sh index-rag"
      echo "  builds the document index: \$LOCAT_RAG_DATA_DIR (default ./data) → \$LOCAT_RAG_INDEX_DIR"
      echo "  rebuilds from scratch; run it again after adding/editing/removing documents" ;;
    get-debug-log)
      echo "usage: ./locat.sh get-debug-log [n]"
      echo "  bundles status + the latest session log + ollama.log into one file under /tmp"
      echo "  n = how many sessions back (default 1 = the most recent); logs live in $STATE_DIR/logs" ;;
    configure)   exec ./configure.sh -h ;;
    consolidate) exec uv run python scripts/consolidate.py -h ;;
  esac
}

cmd_status() {
  local verbose=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -v|--verbose) verbose=1 ;;
      *) echo "locat: unknown status option '$1' (try ./locat.sh status help)" >&2; exit 1 ;;
    esac
    shift
  done

  echo "machine"
  ./configure.sh --hardware

  local pid transport
  local store_desc="${OLLAMA_MODELS}"
  if [[ -L "${LOCAT_MODEL_DIR}/ollama" ]]; then
    store_desc="borrowed -> $(readlink "${LOCAT_MODEL_DIR}/ollama")"
  fi
  pid="$(recorded_pid ollama)"
  if ollama_up; then
    if pid_alive "$pid"; then
      echo "ollama   running (pid $pid), started by locat, store: ${store_desc}"
    else
      echo "ollama   running, NOT started by locat (your own instance; ./locat.sh stop leaves it alone)"
    fi
  else
    echo "ollama   not running (./locat.sh start brings it up), store: ${store_desc}"
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

  echo
  echo "downloaded"
  uv run python scripts/print_models.py -d 2>/dev/null | sed 's/^/  /' \
    || echo "  (could not resolve models — run 'uv sync' and retry)"

  if (( verbose )); then
    echo
    ./configure.sh --catalogs
  fi
}

CMD="${1:-}"
[[ $# -gt 0 ]] && shift
case "${1:-}" in
  help|-h|--help)
    case "$CMD" in
      start|stop|status|index-rag|get-debug-log|configure|consolidate)
        cmd_help "$CMD"; exit 0 ;;
    esac ;;
esac
case "$CMD" in
  start)      cmd_start "$@" ;;
  stop)       cmd_stop ;;
  status)     cmd_status "$@" ;;
  index-rag)  exec uv run python rag.py index ;;
  consolidate) exec uv run python scripts/consolidate.py "$@" ;;
  configure)  exec ./configure.sh "$@" ;;
  get-debug-log) cmd_get_debug_log "$@" ;;
  -h|--help|help|"") usage ;;
  *) echo "locat: unknown command '$CMD' (try ./locat.sh -h)" >&2; exit 1 ;;
esac
