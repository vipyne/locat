#!/usr/bin/env bash
#
# run_ollama.sh — start a local Ollama server whose model store lives INSIDE this
# one model directory ($LOFI_MODEL_DIR/ollama), then pull the conversation LLM
# and the records LLM.
#
# Why relocate the store: the whole point of `lofi` is a self-contained, fully
# offline bot. Keeping the Ollama blobs under $LOFI_MODEL_DIR means every
# checkpoint the bot needs lives in one place — see scripts/model_dir.sh.
#
# Usage:
#   ./scripts/run_ollama.sh            # serve + pull both default models
#   LOFI_LLM_MODEL=qwen2.5:7b ./scripts/run_ollama.sh
#
# The server keeps running in the foreground so you can Ctrl-C to stop it, or run
# the whole script in the background (`./scripts/run_ollama.sh &`). The bot talks
# to it over the OpenAI-compatible endpoint at http://localhost:11434/v1.

set -euo pipefail

# --- locate the repo root (parent of this script's dir) ---------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# --- load .env defaults (LOFI_LLM_MODEL, OLLAMA_HOST); real env vars win ---------
# .env supplies DEFAULTS only — values already in the environment (e.g. set on
# the command line) win, matching config.py's load_dotenv behavior.
load_env_defaults() {
  local file="$1" line key value
  [[ -f "$file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ ^[[:space:]]*(#|$) ]]; then continue; fi
    key="${line%%=*}"; value="${line#*=}"
    if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then continue; fi
    if [[ -z "${!key+x}" ]]; then export "$key=$value"; fi
  done < "$file"
}
load_env_defaults "${LOFI_ENV_FILE:-${REPO_ROOT}/.env}"

# --- config (env-overridable) ------------------------------------------------
# Resolves LOFI_MODEL_DIR and exports OLLAMA_MODELS (plus the other engines'
# stores) so the LLM lands in the same directory as every other model.
LOFI_REPO_ROOT="${REPO_ROOT}"
# shellcheck source=model_dir.sh
. "${SCRIPT_DIR}/model_dir.sh"

OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
export OLLAMA_HOST
# must match config.llm_model()'s default (shell can't import Python)
LOFI_LLM_MODEL="${LOFI_LLM_MODEL:-qwen2.5:7b}"

# Conversation model and the embedder stay co-resident; a swap
# costs a few seconds of weight loading per question.
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-30m}"
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-16384}"
export OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-3}"
# One user, one KV cache per model: parallel slots each hold a separate cache,
# so a new delegation lands on a cold slot and re-prefills the whole analyst
# prompt (43-47s TTFB between delegations vs 1.7s within one). Parallel=1
# keeps every request on the single warm slot.
export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-1}"

mkdir -p "${OLLAMA_MODELS}"

echo "run_ollama: OLLAMA_MODELS=${OLLAMA_MODELS}"
echo "run_ollama: OLLAMA_HOST=${OLLAMA_HOST}"
echo "run_ollama: LOFI_LLM_MODEL=${LOFI_LLM_MODEL}"

# --- is a server already listening on this host? ----------------------------
# `ollama list` returns non-zero if it can't reach a server.
if OLLAMA_HOST="${OLLAMA_HOST}" ollama list >/dev/null 2>&1; then
  echo "run_ollama: an Ollama server is already running on ${OLLAMA_HOST}."
  echo "run_ollama: WARNING — if it was NOT started by this script, its model"
  echo "            store may differ from ${OLLAMA_MODELS}. Stop it first if you"
  echo "            want models pulled into the repo store."
  SERVER_PID=""
else
  echo "run_ollama: starting 'ollama serve' (store → ${OLLAMA_MODELS})..."
  ollama serve &
  SERVER_PID=$!

  # wait (up to ~30s) for the server to accept connections
  for _ in $(seq 1 30); do
    if OLLAMA_HOST="${OLLAMA_HOST}" ollama list >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done

  if ! OLLAMA_HOST="${OLLAMA_HOST}" ollama list >/dev/null 2>&1; then
    echo "run_ollama: ERROR — Ollama server did not become ready in time." >&2
    [[ -n "${SERVER_PID}" ]] && kill "${SERVER_PID}" 2>/dev/null || true
    exit 1
  fi
  echo "run_ollama: server ready (pid ${SERVER_PID})."
fi

# --- pull models (skipped when already in the store) -------------------------
# `ollama pull` dials the registry even for present models, which fails offline.
tag_present() { OLLAMA_HOST="${OLLAMA_HOST}" ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qxF "$1"; }
for tag in "${LOFI_LLM_MODEL}"; do
  if tag_present "${tag}"; then
    echo "run_ollama: '${tag}' already in the store — not pulling."
  else
    echo "run_ollama: pulling '${tag}' (this needs network the first time)..."
    OLLAMA_HOST="${OLLAMA_HOST}" ollama pull "${tag}"
  fi
done

echo "run_ollama: done. Available models:"
OLLAMA_HOST="${OLLAMA_HOST}" ollama list

# --- keep serving if we started the server ----------------------------------
if [[ -n "${SERVER_PID}" ]]; then
  echo "run_ollama: server is running in the foreground (pid ${SERVER_PID})."
  echo "            Press Ctrl-C to stop, or leave it running for the bot."
  wait "${SERVER_PID}"
fi
