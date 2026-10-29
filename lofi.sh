#!/usr/bin/env bash
#
# lofi.sh — the one lifecycle command for the fully-offline voice bot.
#
# Ownership rule: lofi only ever stops PIDs it recorded in $LOFI_STATE_DIR
# (default .lofi/). An Ollama you started yourself is never touched.
#
# Usage:
#   ./lofi.sh setup                       # one-time setup: deps checked, .lofirc/.env seeded, uv sync
#   ./lofi.sh start [-t browser|headphones] [--online-only|--offline-only] [--cascade|--llmwithbackend|--cascade-simple-chat]
#                                         # bring up Ollama if needed, then the bot (default: browser)
#                                         # --online-only: cloud-only sessions, no Ollama, no local models
#                                         # --offline-only: local sessions only; an online connect is refused
#                                         # --cascade: STT, LLM, TTS bot architecture; has tool calls
#                                         # --llmwithbackend: front LLM/analyst LLM bot architecture
#                                         # --cascade-simple-chat: STT, LLM, TTS bot architecture; no tool calls
#   ./lofi.sh stop                        # stop only what lofi started (recorded PIDs)
#   ./lofi.sh status [-v]                 # machine, ownership, bot, models (configured + downloaded), rag; -v adds full catalogs
#   ./lofi.sh index-rag                   # build/rebuild both document indexes
#   ./lofi.sh consolidate [-n]            # adopt models from default HF/Ollama dirs (symlinks)
#   ./lofi.sh configure [args]            # delegates to ./configure.sh
#   ./lofi.sh get-debug-log [n]           # bundle the latest (or nth-latest) session log for debugging
#   ./lofi.sh <command> help              # help for one command
#
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"
# Config files supply DEFAULTS only — values already in the environment (e.g.
# set on the command line) win, matching config.py's load_dotenv behavior.
load_env_defaults() {
  local file="$1" line key value
  [[ -f "$file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ ^[[:space:]]*(#|$) ]]; then continue; fi
    key="${line%%=*}"; value="${line#*=}"
    if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then continue; fi
    # Unquoted value: drop inline ` # comment` and trailing whitespace —
    # python-dotenv does the same, so shell and bot read one value.
    if [[ ! "$value" =~ ^[\"\'] ]]; then
      value="${value%%[[:space:]]\#*}"
      value="${value%"${value##*[![:space:]]}"}"
    fi
    if [[ -z "${!key+x}" ]]; then export "$key=$value"; fi
  done < "$file"
}
# .lofirc holds settings (LOFI_ENV_FILE repoints it), .env the hosted keys.
ENV_FILE="${LOFI_ENV_FILE:-.lofirc}"
load_env_defaults "$ENV_FILE"
load_env_defaults ".env"
# Settings left in .env still apply (both are dotenv loads); cmd_start says
# where they belong.
MISPLACED_SETTINGS=""
if [[ -f .env ]]; then
  MISPLACED_SETTINGS="$(grep -oE '^[[:space:]]*LOFI_[A-Za-z0-9_]+' .env 2>/dev/null | tr -d ' ' | grep -vE '^LOFI_(ONLINE_|PUBLIC_RAG_)' | paste -sd, - || true)"
fi
LOFI_REPO_ROOT="$REPO"
# shellcheck source=scripts/model_dir.sh
. "$REPO/scripts/model_dir.sh"

STATE_DIR="${LOFI_STATE_DIR:-.lofi}"
case "$STATE_DIR" in /*) ;; *) STATE_DIR="$REPO/$STATE_DIR" ;; esac

OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
OLLAMA_BASE="http://${OLLAMA_HOST}"
# must match config.llm_model()'s default (shell can't import Python)
LOFI_LLM_MODEL="${LOFI_LLM_MODEL:-qwen2.5:7b}"
LOFI_WEB_PORT="${LOFI_WEB_PORT:-7860}"
WEB_URL="http://localhost:${LOFI_WEB_PORT}"

usage() { grep '^#   ' "$0" | sed 's/^#   //'; }

ollama_up()   { curl -sf --max-time 2 "$OLLAMA_BASE/api/tags" -o /dev/null 2>/dev/null; }
tag_ready()   { curl -sf --max-time 3 "$OLLAMA_BASE/api/tags" 2>/dev/null | grep -q "\"$1"; }
model_ready() { tag_ready "$LOFI_LLM_MODEL"; }
missing_models() {
  local missing=""
  tag_ready "$LOFI_LLM_MODEL" || missing="$LOFI_LLM_MODEL"
  echo "$missing"
}

truthy() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

# Effective serve mode from LOFI_OFFLINE_ONLY / LOFI_ONLINE_ONLY —
# matches config.serve_mode(): offline-only wins a conflict.
serve_mode() {
  if truthy "${LOFI_OFFLINE_ONLY:-}"; then echo "offline-only"
  elif truthy "${LOFI_ONLINE_ONLY:-}"; then echo "online-only"
  else echo "both"
  fi
}

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
  uv run python ingest.py stats --bare 2>/dev/null \
    || echo "no index (run ./lofi.sh index-rag)"
}

public_rag_line() {
  uv run python ingest.py stats --bare --public 2>/dev/null \
    || echo "no index (run ./lofi.sh index-rag)"
}

# Offline build over all of $LOFI_RAG_DATA_DIR, then the public build over
# data/public through OpenAI when a key is present.
cmd_index_rag() {
  uv run python ingest.py index || exit $?
  if [[ -n "${OPENAI_API_KEY:-}" ]]; then
    uv run python ingest.py index --embedder openai || exit $?
  else
    echo "public index: skipped (no OPENAI_API_KEY in .env) — online mode needs it"
  fi
}

# Setup guard: everything ./lofi.sh setup seeds, reported together.
setup_guard() {
  local mode="$1" missing=0
  if ! command -v uv >/dev/null 2>&1; then
    echo "start: uv not found" >&2; missing=1
  fi
  if [[ "$mode" != "online-only" ]] && ! command -v ollama >/dev/null 2>&1 && ! ollama_up; then
    echo "start: ollama not found" >&2; missing=1
  fi
  if [[ "$ENV_FILE" == ".lofirc" && ! -f .lofirc ]]; then
    echo "start: no .lofirc" >&2; missing=1
  fi
  if [[ ! -d "$REPO/.venv" ]]; then
    echo "start: no .venv" >&2; missing=1
  fi
  if (( missing )); then
    echo "setup needed: run \"./lofi.sh setup\"" >&2
    exit 1
  fi
  if [[ "$mode" != "offline-only" && ! -f .env ]]; then
    echo "start: no .env — online sessions need API keys (./lofi.sh setup seeds it from env.example)"
  fi
}

# Report missing prerequisites from scripts/preflight.py and point at the fix.
preflight_gate() {
  local mode="$1" transport="$2" report kind detail
  local extras="" fetch="" rag=0 rag_public=0 keys=""
  report="$(uv run python scripts/preflight.py --mode "$mode" --transport "$transport" 2>/dev/null)" \
    || { echo "start: preflight failed — run: uv run python scripts/preflight.py" >&2; exit 1; }
  while read -r kind detail; do
    case "$kind" in
      extra)  extras="$extras --extra $detail" ;;
      ollama|stt|tts|daily) fetch="$fetch $kind:${detail##*/}" ;;
      rag)        rag=1 ;;
      rag-public) rag_public=1 ;;
      key)        keys="${keys:+$keys, }$detail" ;;
    esac
  done <<<"$report"

  local fatal=0
  if [[ -n "$extras" ]]; then
    echo "start: engine support not installed — run: uv sync --inexact$extras" >&2; fatal=1
  fi
  if [[ -n "$fetch" ]]; then
    echo "start: not downloaded:$fetch" >&2
    echo "start: fetch once (online): uv run python scripts/prefetch_models.py" >&2; fatal=1
  fi
  if [[ -n "$keys" ]]; then
    if [[ "$mode" == "online-only" ]]; then
      echo "start: online-only needs $keys in .env — add them and rerun" >&2; fatal=1
    else
      echo "start: .env is missing $keys — online sessions will fail until you add them"
    fi
  fi
  if (( fatal )); then exit 1; fi
  # Index notices: the bot starts and chats without an index.
  if (( rag )); then
    echo "start: documents found but no offline index — run: ./lofi.sh index-rag"
  fi
  if (( rag_public )); then
    echo "start: public documents found but no public index — run: ./lofi.sh index-rag"
  fi
  return 0
}

cmd_start() {
  local transport="browser" online_flag=0 offline_flag=0 cascade_flag=0 llmwb_flag=0 simple_chat_flag=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -t|--transport)   transport="${2:-}"; shift 2 || shift ;;
      --online-only)    online_flag=1; shift ;;
      --offline-only)   offline_flag=1; shift ;;
      --cascade)        cascade_flag=1; shift ;;
      --llmwithbackend) llmwb_flag=1; shift ;;
      --cascade-simple-chat) simple_chat_flag=1; shift ;;
      -h|--help)        usage; exit 0 ;;
      *) echo "lofi: unknown start option '$1' (try ./lofi.sh -h)" >&2; exit 1 ;;
    esac
  done
  if (( online_flag && offline_flag )); then
    echo "lofi: --online-only and --offline-only conflict — pick one" >&2; exit 1
  fi
  if (( cascade_flag && llmwb_flag )); then
    echo "lofi: --cascade and --llmwithbackend conflict — pick one" >&2; exit 1
  fi
  if (( simple_chat_flag && cascade_flag )); then
    echo "lofi: --cascade-simple-chat and --cascade conflict — pick one" >&2; exit 1
  fi
  if (( simple_chat_flag && llmwb_flag )); then
    echo "lofi: --cascade-simple-chat and --llmwithbackend conflict — pick one" >&2; exit 1
  fi
  # Flags override the config files; zero the opposite mode var so the
  # offline-wins conflict rule can't flip a flag's choice.
  (( online_flag ))  && export LOFI_ONLINE_ONLY=1 LOFI_OFFLINE_ONLY=0
  (( offline_flag )) && export LOFI_OFFLINE_ONLY=1 LOFI_ONLINE_ONLY=0
  # Arch flags steer both session kinds: LOFI_ARCHITECTURE (offline) and
  # LOFI_ONLINE_ARCHITECTURE (online) each default to llmwithbackend.
  # simple-chat is offline-only, so it leaves LOFI_ONLINE_ARCHITECTURE alone.
  (( cascade_flag )) && export LOFI_ARCHITECTURE=cascade LOFI_ONLINE_ARCHITECTURE=cascade
  (( llmwb_flag ))   && export LOFI_ARCHITECTURE=llmwithbackend LOFI_ONLINE_ARCHITECTURE=llmwithbackend
  (( simple_chat_flag )) && export LOFI_ARCHITECTURE=cascade-simple-chat
  if [[ "${LOFI_ARCHITECTURE:-llmwithbackend}" == "cascade-simple-chat" ]]; then
    if truthy "${LOFI_ONLINE_ONLY:-}"; then
      export LOFI_ONLINE_ONLY=0
      echo "start: simple-chat is offline — overriding online-only."
    fi
  fi

  local mode
  mode="$(serve_mode)"
  if [[ "$mode" == "offline-only" ]] && truthy "${LOFI_ONLINE_ONLY:-}"; then
    echo "start: LOFI_ONLINE_ONLY and LOFI_OFFLINE_ONLY are both set — offline-only wins."
  fi

  case "$transport" in
    browser|headphones) ;;
    *) echo "lofi: unknown transport '$transport' — valid: browser (default), headphones" >&2; exit 1 ;;
  esac
  # The local-audio bot has no online stack to run.
  if [[ "$mode" == "online-only" && "$transport" == "headphones" ]]; then
    echo "lofi: online-only (--online-only / LOFI_ONLINE_ONLY=1) needs the browser bot — drop '-t headphones'" >&2; exit 1
  fi

  setup_guard "$mode"

  mkdir -p "$STATE_DIR"

  if [[ -n "$MISPLACED_SETTINGS" ]]; then
    echo "start: offline settings belong in .lofirc — .env holds the keys and online-session config; move from .env: $MISPLACED_SETTINGS (they still apply)" >&2
  fi

  local bot_pid
  bot_pid="$(recorded_pid bot)"
  if pid_alive "$bot_pid"; then
    echo "start: bot already running (pid $bot_pid) — run ./lofi.sh stop first." >&2
    exit 1
  fi

  # Port guard: browser bot binds $LOFI_WEB_PORT; a listener there means a
  # bot lofi didn't record (ownership rule: lofi kills only recorded pids).
  if [[ "$transport" == "browser" ]] && command -v lsof >/dev/null 2>&1; then
    local port_pid port_cmd
    port_pid="$(lsof -nP -tiTCP:"$LOFI_WEB_PORT" -sTCP:LISTEN 2>/dev/null | head -n1 || true)"
    if [[ -n "$port_pid" ]]; then
      port_cmd="$(ps -o command= -p "$port_pid" 2>/dev/null || true)"
      if echo "$port_cmd" | grep -q "bot_browser.py"; then
        echo "start: port $LOFI_WEB_PORT already serves a lofi bot (pid $port_pid) lofi didn't start" >&2
        echo "start: stop it with: kill $port_pid   (or ./lofi.sh stop in the checkout that started it)" >&2
      else
        echo "start: port $LOFI_WEB_PORT in use by pid $port_pid: ${port_cmd:-?}" >&2
        echo "start: free the port, or pick another: LOFI_WEB_PORT=7861 ./lofi.sh start" >&2
      fi
      exit 1
    fi
  fi

  preflight_gate "$mode" "$transport"

  if [[ "$mode" == "online-only" ]]; then
    echo "start: online-only — sessions run on Deepgram/OpenAI/Cartesia; Ollama stays down."
    echo "start: pick ☁️ online on the page; an offline connect is refused."
  else
    if [[ "$mode" == "offline-only" ]]; then
      echo "start: offline-only — pick 🔒 offline on the page; an online connect is refused."
    fi
    if ! ollama_up; then
      echo "start: bringing up Ollama (store → ${OLLAMA_MODELS}; log → ollama.log)"
      nohup ./scripts/run_ollama.sh >"$REPO/ollama.log" 2>&1 &
      echo $! >"$STATE_DIR/ollama.pid"
      echo "start: waiting for Ollama to come up..."
      for _ in $(seq 1 120); do ollama_up && break; sleep 1; done
      ollama_up || { echo "start: Ollama did not come up — see ollama.log" >&2; exit 1; }
    fi
    # Preflight verified the weights on disk — the server only has to list them.
    if ! model_ready; then
      echo "start: waiting for $(missing_models) to be served..."
      for _ in $(seq 1 60); do model_ready && break; sleep 1; done
      model_ready || { echo "start: $(missing_models) not served — see ollama.log" >&2; exit 1; }
    fi

    # Fail before launch when any configured model is still missing on disk.
    local models_out
    models_out="$(model_lines)"
    printf '%s\n' "$models_out"
    if grep -qF '(not downloaded yet)' <<<"$models_out"; then
      echo "start: models above are not downloaded — fetch once: uv run python scripts/prefetch_models.py" >&2
      exit 1
    fi
  fi

  mkdir -p "$STATE_DIR/logs"
  if [[ -f "$STATE_DIR/bot.log" && ! -L "$STATE_DIR/bot.log" ]]; then
    mv "$STATE_DIR/bot.log" "$STATE_DIR/logs/bot-00000000-pre-upgrade.log"
  fi
  local bot_log="$STATE_DIR/logs/bot-$(date +%Y%m%d-%H%M%S).log"
  ln -sf "$bot_log" "$STATE_DIR/bot.log"
  session_logs | tail -n +11 | xargs rm -f
  case "$transport" in
    browser)
      # No -t pin: the runner then serves every installed browser transport
      # (SmallWebRTC, websocket) and the client picks per session via
      # /start — the webrtc → websocket fallback path needs this.
      nohup uv run python bot_browser.py --host localhost --port "$LOFI_WEB_PORT" >"$bot_log" 2>&1 &
      ;;
    headphones)
      echo "start: launching the local-audio bot — use headphones 🎧"
      nohup uv run python bot.py >"$bot_log" 2>&1 &
      ;;
  esac
  echo $! >"$STATE_DIR/bot.pid"
  echo "$transport" >"$STATE_DIR/bot.transport"
  echo "start: bot starting (pid $(recorded_pid bot), log → ${bot_log#"$REPO"/})"
  echo "start: stop everything lofi started with ./lofi.sh stop"

  if [[ "$transport" == "browser" ]]; then
    for _ in $(seq 1 120); do
      curl -sf --max-time 2 "$WEB_URL/" -o /dev/null 2>/dev/null && break
      pid_alive "$(recorded_pid bot)" || { echo "start: bot exited early — see ${bot_log#"$REPO"/}" >&2; exit 1; }
      sleep 1
    done
    echo "start: ▶ $WEB_URL — allow the mic and Connect."
    # Auto-open the page. LOFI_OPEN_BROWSER=0 disables. Skipped on WSL: the
    # right browser lives on the Windows side, which this shell can't open —
    # xdg-open would launch a WSLg browser inside the VM instead.
    if [[ "${LOFI_OPEN_BROWSER:-1}" == "0" ]]; then
      :
    elif grep -qi microsoft /proc/version 2>/dev/null; then
      echo "start: (WSL detected — open the URL above in a WINDOWS browser; add ?transport=websocket for audio)"
    elif command -v open >/dev/null 2>&1; then
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
        echo "stop: pid $pid in $name.pid now runs '$cmd' — not lofi's, removing the pidfile only."
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
    echo "ollama: running, NOT started by lofi — leaving it alone."
  fi
  [[ "$stopped" -eq 0 ]] && echo "stop: nothing lofi started is running."
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
    echo "get-debug-log: no session logs in $STATE_DIR/logs — start the bot at least once (./lofi.sh start)" >&2
    exit 1
  fi
  bundle="/tmp/lofi-debug-$(date +%Y%m%d-%H%M%S).log"
  {
    echo "=== lofi debug bundle · $(date) ==="
    echo "=== session log: $session ==="
    echo
    echo "--- lofi status at collection time ---"
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
    setup)
      echo "usage: ./lofi.sh setup"
      echo "  one-time setup for a fresh clone: checks uv + ollama (install hints when"
      echo "  absent), copies .lofirc and .env from their examples, creates data/private,"
      echo "  runs uv sync; safe to rerun"
      echo "  models are the separate online step: uv run python scripts/prefetch_models.py" ;;
    start)
      echo "usage: ./lofi.sh start [-t browser|headphones] [--online-only|--offline-only] [--cascade|--llmwithbackend|--cascade-simple-chat]"
      echo "  -t, --transport   browser (the default — serves websocket for offline"
      echo "                    sessions and SmallWebRTC for online sessions)"
      echo "                    headphones (local mic/speakers via PyAudio — wear headphones)"
      echo "  --online-only     serve online sessions only (LOFI_ONLINE_ONLY=1): no Ollama,"
      echo "                    no local model preflights; an offline connect is refused"
      echo "  --offline-only    serve offline sessions only (LOFI_OFFLINE_ONLY=1): local stack"
      echo "                    as usual; an online connect is refused"
      echo "  --cascade         STT, LLM, TTS (LOFI_ARCHITECTURE / LOFI_ONLINE_ARCHITECTURE=cascade)"
      echo "  --llmwithbackend  front model + analyst backend, default"
      echo "                    (LOFI_ARCHITECTURE / LOFI_ONLINE_ARCHITECTURE=llmwithbackend)"
      echo "  --cascade-simple-chat  STT, LLM, TTS with no tools — chat only, offline only"
      echo "                    (LOFI_ARCHITECTURE=cascade-simple-chat; overrides online-only)"
      echo "  flags win over .lofirc; both vars set means offline-only wins"
      echo "  checks prerequisites first: a fresh clone gets 'setup needed: run \"./lofi.sh setup\"',"
      echo "  missing models/extras/indexes get the exact command to run"
      echo "  brings up Ollama first if nothing answers on \$OLLAMA_HOST (and records its pid)" ;;
    stop)
      echo "usage: ./lofi.sh stop"
      echo "  stops ONLY processes lofi started (pids recorded in $STATE_DIR)"
      echo "  an Ollama you started yourself is reported and left alone" ;;
    status)
      echo "usage: ./lofi.sh status [-v]"
      echo "  machine hardware, ollama (ownership + store), bot, configured models (full paths),"
      echo "  rag index, and an inventory of every model on disk with sizes"
      echo "  -v, --verbose   also print the full STT/LLM/TTS catalogs (every model, even ones"
      echo "                  too big for this machine)" ;;
    index-rag)
      echo "usage: ./lofi.sh index-rag"
      echo "  builds the offline index: \$LOFI_RAG_DATA_DIR (default ./data) → \$LOFI_RAG_INDEX_DIR/lofi.db"
      echo "  then the public index for online mode: ./data/public → lofi-public.db, embedded"
      echo "  through OpenAI (skipped with a notice when OPENAI_API_KEY is unset)"
      echo "  rebuilds from scratch; run it again after adding/editing/removing documents" ;;
    get-debug-log)
      echo "usage: ./lofi.sh get-debug-log [n]"
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
      *) echo "lofi: unknown status option '$1' (try ./lofi.sh status help)" >&2; exit 1 ;;
    esac
    shift
  done

  echo "machine"
  ./configure.sh --hardware

  local pid transport
  local store_desc="${OLLAMA_MODELS}"
  if [[ -L "${LOFI_MODEL_DIR}/ollama" ]]; then
    store_desc="borrowed -> $(readlink "${LOFI_MODEL_DIR}/ollama")"
  fi
  pid="$(recorded_pid ollama)"
  if ollama_up; then
    if pid_alive "$pid"; then
      echo "ollama   running (pid $pid), started by lofi, store: ${store_desc}"
    else
      echo "ollama   running, NOT started by lofi (your own instance; ./lofi.sh stop leaves it alone)"
    fi
  else
    echo "ollama   not running (./lofi.sh start brings it up), store: ${store_desc}"
  fi

  pid="$(recorded_pid bot)"
  if pid_alive "$pid"; then
    transport="$(cat "$STATE_DIR/bot.transport" 2>/dev/null || echo "?")"
    if [[ "$transport" == "browser" ]]; then
      echo "bot      running (pid $pid), transport: browser, $WEB_URL"
    else
      echo "bot      running (pid $pid), transport: $transport"
    fi
  else
    echo "bot      not running (./lofi.sh start)"
  fi

  model_lines --bare
  echo "rag      $(rag_line)"
  echo "  public $(public_rag_line)"

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
      setup|start|stop|status|index-rag|get-debug-log|configure|consolidate)
        cmd_help "$CMD"; exit 0 ;;
    esac ;;
esac
case "$CMD" in
  setup)      exec ./setup.sh ;;
  start)      cmd_start "$@" ;;
  stop)       cmd_stop ;;
  status)     cmd_status "$@" ;;
  index-rag)  cmd_index_rag ;;
  consolidate) exec uv run python scripts/consolidate.py "$@" ;;
  configure)  exec ./configure.sh "$@" ;;
  get-debug-log) cmd_get_debug_log "$@" ;;
  -h|--help|help|"") usage ;;
  *) echo "lofi: unknown command '$CMD' (try ./lofi.sh -h)" >&2; exit 1 ;;
esac
