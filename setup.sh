#!/usr/bin/env bash
#
# setup.sh — one-time setup for a fresh clone (README → Setup):
# checks uv + Ollama (install hints when absent), seeds .lofirc and .env from
# their examples, creates data/private, runs uv sync. ./lofi.sh setup lands here.
#
# Model downloads are the separate one-time online step:
#   uv run python scripts/prefetch_models.py
#
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"

missing_dep=0
if ! command -v uv >/dev/null 2>&1; then
  echo "setup: uv not found — install it:" >&2
  echo "         curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  missing_dep=1
fi
if ! command -v ollama >/dev/null 2>&1; then
  echo "setup: ollama not found — install it from https://ollama.com" >&2
  echo "         (Linux/WSL2: curl -fsSL https://ollama.com/install.sh | sh)" >&2
  missing_dep=1
fi
if (( missing_dep )); then
  exit 1
fi

if [[ ! -f .lofirc ]]; then
  cp lofirc.example .lofirc
  echo "setup: created .lofirc from lofirc.example (edit it to change models)"
fi
if [[ ! -f .env ]]; then
  cp env.example .env
  echo "setup: created .env from env.example (fill in the API keys for online sessions)"
fi
mkdir -p data/private

echo "setup: creating the Python environment (uv sync)..."
# --inexact keeps optional extras (moonshine/piper/local-audio) from an earlier run.
uv sync --inexact || { echo "setup: uv sync failed — see the error above" >&2; exit 1; }

echo "setup: done. Next, the one-time online step (~12 GB of default models):"
echo "         uv run python scripts/prefetch_models.py"
echo "       then drop documents in data/ and build the index:"
echo "         ./lofi.sh index-rag"
echo "       then:"
echo "         ./lofi.sh start"
