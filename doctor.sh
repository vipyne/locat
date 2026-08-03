#!/usr/bin/env bash
#
# doctor.sh — report what this machine can handle for the offline voice bot.
#
# Checks the hard requirement (Apple Silicon — Whisper-MLX is MLX-only), then
# compares total RAM against the configured LLM and suggests the best-fitting
# qwen2.5 tag. Verbose mode adds GPU/disk/tooling checks and a capability
# table. Read-only: never launches the bot or Ollama.
#
# Usage:
#   ./doctor.sh          # pass/fail + model advice
#   ./doctor.sh -v       # full capability matrix
#
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"
[[ -f .env ]] && { set -a; source .env; set +a; }

VERBOSE=0
case "${1:-}" in
  -v|--verbose) VERBOSE=1 ;;
  -h|--help)    grep '^#   ' "$0" | sed 's/^#   //'; exit 0 ;;
  "")           ;;
  *) echo "doctor: unknown option '$1' (try ./doctor.sh -h)" >&2; exit 1 ;;
esac

pass() { echo "  ✅ $*"; }
warn() { echo "  ⚠️  $*"; }

echo "doctor: hardware check"

# --- Platform: Whisper-MLX (and MLX itself) require an Apple Silicon Mac ----
OS="$(uname -s)"; ARCH="$(uname -m)"
if [[ "$OS" == "Darwin" && "$ARCH" == "arm64" ]]; then
  CHIP="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo 'Apple Silicon')"
  pass "Apple Silicon Mac (${CHIP})"
else
  echo "  ❌ This is ${OS}/${ARCH} — the bot's STT (Whisper-MLX) requires an Apple"
  echo "     Silicon Mac; MLX does not run here. On Linux, a different Whisper"
  echo "     backend would be needed (not currently wired up)."
  echo
  echo "doctor: ❌ this machine cannot run the bot as configured"
  exit 1
fi

# --- RAM vs the configured LLM ---------------------------------------------
LLM_MODEL="${LLM_MODEL:-qwen2.5:14b}"
RAM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))

# Best qwen2.5 tag for this much unified memory, leaving headroom for Whisper
# (~1.6 GB), Kokoro (~0.3 GB), and the OS.
if   (( RAM_GB >= 24 )); then RECOMMEND="qwen2.5:14b"
elif (( RAM_GB >= 12 )); then RECOMMEND="qwen2.5:7b"
else                          RECOMMEND="qwen2.5:3b"
fi
pass "${RAM_GB} GB unified memory — recommended LLM: ${RECOMMEND}"

# Approximate in-memory size (GB, q4 quant) of a qwen2.5 tag; 0 = unknown.
llm_needs_gb() {
  case "$1" in
    *72b*) echo 47 ;; *32b*) echo 20 ;; *14b*) echo 10 ;;
    *7b*)  echo 5  ;; *3b*)  echo 2  ;; *1.5b*) echo 1 ;; *0.5b*) echo 1 ;;
    *)     echo 0  ;;
  esac
}
NEED="$(llm_needs_gb "$LLM_MODEL")"
if (( NEED == 0 )); then
  warn "configured LLM '${LLM_MODEL}': size unknown — can't judge fit"
elif (( NEED + 4 > RAM_GB )); then
  warn "configured LLM '${LLM_MODEL}' wants ~${NEED} GB + overhead — tight on ${RAM_GB} GB; consider ${RECOMMEND}"
else
  pass "configured LLM '${LLM_MODEL}' (~${NEED} GB) fits comfortably"
fi

echo
uv run python scripts/print_models.py 2>/dev/null \
  || warn "could not resolve models — run 'uv sync'"

# --- Verbose: full capability matrix ---------------------------------------
if (( VERBOSE )); then
  echo
  echo "doctor: capability matrix"

  GPU_CORES="$(system_profiler SPDisplaysDataType 2>/dev/null \
    | awk -F': ' '/Total Number of Cores/{print $2; exit}' || :)"
  echo "  GPU cores:  ${GPU_CORES:-unknown}"
  echo "  free disk:  $(df -h . | awk 'NR==2{print $4}') available on this volume"
  if [[ -d models ]]; then
    echo "  ./models:"
    du -sh models/*/ 2>/dev/null | awk '{printf "     %-8s %s\n", $1, $2}' || :
  fi

  echo "  tooling:"
  for tool in uv ollama curl; do
    if command -v "$tool" >/dev/null 2>&1; then
      echo "     ✅ $tool ($(command -v "$tool"))"
    else
      echo "     ❌ $tool — not on PATH"
    fi
  done

  echo "  qwen2.5 tags on ${RAM_GB} GB (q4, ~2 GB STT/TTS + OS headroom):"
  for tag in 3b 7b 14b 32b 72b; do
    need="$(llm_needs_gb "qwen2.5:$tag")"
    if   (( need + 6 <= RAM_GB )); then verdict="✅ comfortable"
    elif (( need + 4 <= RAM_GB )); then verdict="⚠️  tight"
    else                                verdict="❌ too big"
    fi
    printf "     %-12s ~%2d GB  %s\n" "qwen2.5:$tag" "$need" "$verdict"
  done

  echo "  Whisper-MLX variants (all fine on ≥8 GB unless marked):"
  printf "     %-18s ~0.2 GB  ✅\n" "TINY"
  printf "     %-18s ~1.5 GB  ✅\n" "MEDIUM"
  printf "     %-18s ~3.0 GB  %s\n" "LARGE_V3" "$( (( RAM_GB >= 16 )) && echo '✅' || echo '⚠️  tight' )"
  printf "     %-18s ~1.6 GB  ✅  (default)\n" "LARGE_V3_TURBO"
  printf "     %-18s ~0.6 GB  ✅\n" "LARGE_V3_TURBO_Q4"
fi

echo
echo "doctor: ✅ good to go — ./start.sh"
