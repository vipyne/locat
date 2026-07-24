#!/usr/bin/env bash
#
# stop.sh — stop the repo-local Ollama server that start.sh launched.
#
set -euo pipefail
if pkill -f "ollama serve" 2>/dev/null; then
  echo "stop: stopped the background Ollama server."
else
  echo "stop: no 'ollama serve' process found (already stopped)."
fi
