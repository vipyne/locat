#!/usr/bin/env bash
#
# model_dir.sh — resolve LOCAT_MODEL_DIR and export every model-store variable.
#
# SOURCE this, don't execute it:
#
#     LOCAT_REPO_ROOT="$REPO"; . "$REPO/scripts/model_dir.sh"
#
# One directory holds every model this project downloads, and it can live
# anywhere on the machine:
#
#     $LOCAT_MODEL_DIR/
#     ├── huggingface/   HF_HOME            (Whisper-MLX, faster-whisper, Moonshine)
#     ├── kokoro/        KOKORO_*_PATH      (Kokoro TTS)
#     ├── piper/         LOCAT_PIPER_DOWNLOAD_DIR (Piper TTS voices)
#     └── ollama/        OLLAMA_MODELS      (the LLM)
#
# config.py resolves the same variable identically for the Python side; this
# file exists because run_ollama.sh / configure.sh need it too and cannot import
# config.py — they have to work before `uv sync` has built a venv. Keep the two
# resolvers in step: relative paths resolve against the REPO ROOT (not the
# caller's cwd, so it behaves the same however the script was invoked), and a
# leading ~ is expanded.
#
# Every derived variable uses :- so an explicitly-set HF_HOME / OLLAMA_MODELS /
# etc. still wins over the LOCAT_MODEL_DIR default.

# Collapse "", "." and ".." segments in an absolute path, textually. Matches
# Python's os.path.normpath, which config.py uses — deliberately NOT `pwd -P` or
# realpath: those resolve symlinks (so /tmp/x would come back as /private/tmp/x
# on macOS) and only work on paths that already exist. Staying lexical keeps the
# two resolvers in agreement and echoes back the path the user actually typed.
locat__normpath() {
  local path="$1" seg out="" old_ifs="$IFS" reset_glob=""
  # `for seg in $path` is an unquoted expansion: disable globbing so a literal
  # * in a directory name cannot expand against the cwd.
  case "$-" in *f*) ;; *) reset_glob=1 ;; esac
  set -f
  IFS='/'
  for seg in $path; do
    case "$seg" in
      "" | ".") ;;
      "..") out="${out%/*}" ;;
      *) out="$out/$seg" ;;
    esac
  done
  IFS="$old_ifs"
  [[ -n "$reset_glob" ]] && set +f
  printf '%s\n' "${out:-/}"
}

locat_resolve_model_dir() {  # $1 = repo root
  local repo="$1" raw="${LOCAT_MODEL_DIR:-}"
  [[ -z "$raw" ]] && raw="./models"

  # bash expands ~ only in literals, never in a variable's value — do it here.
  case "$raw" in
    "~")   raw="$HOME" ;;
    "~/"*) raw="$HOME/${raw#"~/"}" ;;
  esac

  # Relative paths hang off the repo root, so `./models` means the same thing
  # whether you ran ./configure.sh from the repo or from three levels down.
  case "$raw" in
    /*) ;;
    *) raw="$repo/$raw" ;;
  esac

  # Never creates anything — callers decide when to mkdir.
  locat__normpath "$raw"
}

LOCAT_MODEL_DIR="$(locat_resolve_model_dir "${LOCAT_REPO_ROOT:-$PWD}")"
export LOCAT_MODEL_DIR
export HF_HOME="${HF_HOME:-${LOCAT_MODEL_DIR}/huggingface}"
export OLLAMA_MODELS="${OLLAMA_MODELS:-${LOCAT_MODEL_DIR}/ollama}"
export LOCAT_PIPER_DOWNLOAD_DIR="${LOCAT_PIPER_DOWNLOAD_DIR:-${LOCAT_MODEL_DIR}/piper}"
export LOCAT_KOKORO_MODEL_PATH="${LOCAT_KOKORO_MODEL_PATH:-${LOCAT_MODEL_DIR}/kokoro/kokoro-v1.0.onnx}"
export LOCAT_KOKORO_VOICES_PATH="${LOCAT_KOKORO_VOICES_PATH:-${LOCAT_MODEL_DIR}/kokoro/voices-v1.0.bin}"
