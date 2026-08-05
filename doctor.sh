#!/usr/bin/env bash
#
# doctor.sh — report what this machine can handle for the offline voice bot.
#
# Checks the hard requirement (Apple Silicon — Whisper-MLX is MLX-only), then
# compares total RAM against the configured LLM and suggests the best-fitting
# qwen2.5 tag. Verbose mode adds a full hardware profile (CPU/GPU cores,
# estimated memory bandwidth, disk) and a curated LLM catalog ranked by what
# fits THIS machine — both memory footprint and estimated speech latency.
# Interactive mode walks through choosing an STT / LLM / TTS-voice combo,
# approves it against the hardware, and (only with your explicit confirmation)
# writes it to .env and pulls missing models. Without -i it never writes.
#
# Usage:
#   ./doctor.sh          # pass/fail + model advice
#   ./doctor.sh -v       # full capability matrix + model catalog
#   ./doctor.sh -i       # interactively pick & approve an STT/LLM/TTS combo
#
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"
[[ -f .env ]] && { set -a; source .env; set +a; }

VERBOSE=0
INTERACTIVE=0
case "${1:-}" in
  -v|--verbose)     VERBOSE=1 ;;
  -i|--interactive) INTERACTIVE=1 ;;
  -h|--help)        grep '^#   ' "$0" | sed 's/^#   //'; exit 0 ;;
  "")               ;;
  *) echo "doctor: unknown option '$1' (try ./doctor.sh -h)" >&2; exit 1 ;;
esac

pass() { echo "  ✅ $*"; }
warn() { echo "  ⚠️  $*"; }

# --- Curated model data ------------------------------------------------------
# Ollama has no catalog API (its library is a website), so doctor ships a
# hand-picked, offline-usable table instead. tag|~GB in memory (q4)|note
LLM_CATALOG=(
  "qwen2.5:0.5b|1|"
  "qwen2.5:1.5b|1|"
  "qwen2.5:3b|2|"
  "qwen2.5:7b|5|"
  "qwen2.5:14b|10|project default"
  "qwen2.5:32b|20|"
  "qwen2.5:72b|47|"
  "qwen3:0.6b|1|hybrid thinking"
  "qwen3:1.7b|2|hybrid thinking"
  "qwen3:4b|3|hybrid thinking"
  "qwen3:8b|6|hybrid thinking"
  "qwen3:14b|10|hybrid thinking"
  "qwen3:32b|20|hybrid thinking"
  "llama3.2:1b|1|"
  "llama3.2:3b|2|"
  "llama3.1:8b|5|"
  "llama3.1:70b|43|"
  "gemma3:1b|1|"
  "gemma3:4b|3|"
  "gemma3:12b|8|"
  "gemma3:27b|17|"
  "mistral:7b|4|"
  "mistral-small:24b|15|"
  "phi4:14b|9|"
  "phi4-mini:3.8b|3|"
  "granite3.3:2b|2|"
  "granite3.3:8b|5|"
  "smollm2:1.7b|1|"
  "deepseek-r1:1.5b|1|reasoning: thinks before speaking"
  "deepseek-r1:7b|5|reasoning: thinks before speaking"
  "deepseek-r1:14b|10|reasoning: thinks before speaking"
  "deepseek-r1:32b|20|reasoning: thinks before speaking"
  "deepseek-r1:70b|43|reasoning: thinks before speaking"
)

# Whisper-MLX variants: MLXModel member|display GB|GB rounded up|HF repo dir
WHISPER_TABLE=(
  "TINY|0.2|1|whisper-tiny"
  "MEDIUM|1.5|2|whisper-medium-mlx"
  "LARGE_V3|3.0|3|whisper-large-v3-mlx"
  "LARGE_V3_TURBO|1.6|2|whisper-large-v3-turbo"
  "LARGE_V3_TURBO_Q4|0.6|1|whisper-large-v3-turbo-q4"
)
DEFAULT_WHISPER="LARGE_V3_TURBO"

# Common Kokoro voice ids (voice choice never affects fit — the model is a
# constant ~0.3 GB regardless).
KOKORO_VOICES=(af_heart af_bella af_nicole af_sky am_adam am_michael bf_emma bm_george)
DEFAULT_VOICE="af_heart"

# Approximate in-memory size (GB, q4 quant) of an LLM tag; 0 = unknown.
llm_needs_gb() {
  local entry
  for entry in "${LLM_CATALOG[@]}"; do
    [[ "${entry%%|*}" == "$1" ]] && { echo "$entry" | cut -d'|' -f2; return; }
  done
  case "$1" in  # size-suffix fallback for tags not in the catalog
    *72b*) echo 47 ;; *70b*) echo 43 ;; *32b*) echo 20 ;; *14b*) echo 10 ;;
    *8b*)  echo 6  ;; *7b*)  echo 5  ;; *3b*)  echo 2  ;; *1.5b*) echo 1 ;;
    *1b*)  echo 1  ;; *0.5b*) echo 1 ;; *)    echo 0  ;;
  esac
}

# --- Platform: Whisper-MLX (and MLX itself) require an Apple Silicon Mac ----
OS="$(uname -s)"; ARCH="$(uname -m)"
if [[ "$OS" != "Darwin" || "$ARCH" != "arm64" ]]; then
  echo "doctor: hardware check"
  echo "  ❌ This is ${OS}/${ARCH} — the bot's STT (Whisper-MLX) requires an Apple"
  echo "     Silicon Mac; MLX does not run here. On Linux, a different Whisper"
  echo "     backend would be needed (not currently wired up)."
  echo
  echo "doctor: ❌ this machine cannot run the bot as configured"
  exit 1
fi

# --- Hardware profile (shared by every mode) --------------------------------
CHIP="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo 'Apple Silicon')"
RAM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
GPU_CORES="$(system_profiler SPDisplaysDataType 2>/dev/null \
  | awk -F': ' '/Total Number of Cores/{print $2; exit}' || :)"
CPU_CORES="$(sysctl -n hw.physicalcpu 2>/dev/null || :)"
CPU_PERF="$(sysctl -n hw.perflevel0.physicalcpu 2>/dev/null || :)"
CPU_EFF="$(sysctl -n hw.perflevel1.physicalcpu 2>/dev/null || :)"
MACOS_VER="$(sw_vers -productVersion 2>/dev/null || :)"
FREE_DISK="$(df -h . | awk 'NR==2{print $4}')"

# Unified-memory bandwidth (GB/s) by chip family — decode speed of a q4 LLM is
# bandwidth-bound, so this single number predicts tokens/sec.
BW_EST=""  # non-empty when we fell back to a guess
case "$CHIP" in
  *M1\ Ultra*) BW=800 ;; *M1\ Max*) BW=400 ;; *M1\ Pro*) BW=200 ;; *M1*) BW=68  ;;
  *M2\ Ultra*) BW=800 ;; *M2\ Max*) BW=400 ;; *M2\ Pro*) BW=200 ;; *M2*) BW=100 ;;
  *M3\ Ultra*) BW=800 ;; *M3\ Max*) BW=400 ;; *M3\ Pro*) BW=150 ;; *M3*) BW=100 ;;
                        *M4\ Max*) BW=546 ;; *M4\ Pro*) BW=273 ;; *M4*) BW=120 ;;
  *) BW=100; BW_EST="unrecognized chip — assuming" ;;
esac

# Rough decode speed for a q4 model of $1 GB on this chip's bandwidth.
est_tok_s() { echo $(( BW / ( $1 > 0 ? $1 : 1 ) )); }

# Fit verdict for an LLM of $1 GB: prints "rank|verdict" (rank sorts: 0 best).
# RAM headroom mirrors the combo math: ~2 GB STT/TTS + ~4 GB OS.
llm_verdict() {
  local gb=$1 tok; tok="$(est_tok_s "$gb")"
  if   (( gb + 4 > RAM_GB )); then echo "3|❌ too big"
  elif (( tok < 8 ));          then echo "2|🐢 too slow for voice"
  elif (( gb + 6 > RAM_GB ));  then echo "1|⚠️  tight fit"
  elif (( tok < 15 ));         then echo "1|⚠️  sluggish"
  else                              echo "0|✅ good"
  fi
}

# --- Installed-model detection (read-only: never starts a server) -----------
OLLAMA_MODELS="${OLLAMA_MODELS:-$REPO/models/ollama}"
INSTALLED_TAGS=""
if command -v ollama >/dev/null 2>&1 && ollama list >/dev/null 2>&1; then
  INSTALLED_TAGS="$(ollama list 2>/dev/null | awk 'NR>1{print $1}')"
elif [[ -d "$OLLAMA_MODELS/manifests" ]]; then
  # No server running — read the repo-local store's manifest tree directly.
  INSTALLED_TAGS="$(find "$OLLAMA_MODELS/manifests" -mindepth 4 -maxdepth 4 -type f 2>/dev/null \
    | awk -F/ '{print $(NF-1)":"$NF}')"
fi

llm_installed() {
  local t
  for t in $INSTALLED_TAGS; do [[ "$t" == "$1" ]] && return 0; done
  return 1
}

whisper_installed() {  # $1 = HF repo dir suffix, e.g. whisper-large-v3-turbo
  [[ -d "${HF_HOME:-$REPO/models/huggingface}/hub/models--mlx-community--$1" ]]
}

# Catalog rows, best-fitting first: "rank|gb|tag|tok/s|verdict|installed|note"
sorted_catalog_rows() {
  local entry tag gb note tok rv rank verdict inst
  for entry in "${LLM_CATALOG[@]}"; do
    tag="$(echo "$entry" | cut -d'|' -f1)"
    gb="$(echo "$entry"  | cut -d'|' -f2)"
    note="$(echo "$entry" | cut -d'|' -f3)"
    tok="$(est_tok_s "$gb")"
    rv="$(llm_verdict "$gb")"; rank="${rv%%|*}"; verdict="${rv#*|}"
    inst=""; llm_installed "$tag" && inst="(installed)"
    echo "$rank|$gb|$tag|$tok|$verdict|$inst|$note"
  done | sort -t'|' -k1,1n -k2,2rn
}

print_catalog_table() {  # $1 = "numbered" to prefix row numbers (for -i)
  local i=0 row gb tag tok verdict inst note prefix
  local OLD_IFS="$IFS"; IFS=$'\n'
  for row in $(sorted_catalog_rows); do
    IFS="$OLD_IFS"
    i=$((i + 1))
    gb="$(echo "$row" | cut -d'|' -f2)";  tag="$(echo "$row" | cut -d'|' -f3)"
    tok="$(echo "$row" | cut -d'|' -f4)"; verdict="$(echo "$row" | cut -d'|' -f5)"
    inst="$(echo "$row" | cut -d'|' -f6)"; note="$(echo "$row" | cut -d'|' -f7)"
    prefix="   "
    [[ "${1:-}" == "numbered" ]] && prefix="$(printf '%3d)' "$i")"
    printf "  %s %-18s ~%2d GB  ~%3d tok/s  %-22s %-12s %s\n" \
      "$prefix" "$tag" "$gb" "$tok" "$verdict" "$inst" "$note"
    IFS=$'\n'
  done
  IFS="$OLD_IFS"
}

print_hardware_profile() {
  echo "  chip:       ${CHIP}"
  echo "  macOS:      ${MACOS_VER:-unknown}"
  echo "  memory:     ${RAM_GB} GB unified"
  if [[ -n "$CPU_PERF" && -n "$CPU_EFF" ]]; then
    echo "  CPU cores:  ${CPU_CORES:-?} (${CPU_PERF} performance + ${CPU_EFF} efficiency)"
  else
    echo "  CPU cores:  ${CPU_CORES:-unknown}"
  fi
  echo "  GPU cores:  ${GPU_CORES:-unknown}"
  echo "  memory bw:  ~${BW} GB/s ${BW_EST:+(${BW_EST} baseline) }(est. — governs LLM tokens/sec)"
  echo "  free disk:  ${FREE_DISK} available on this volume"
}

# =============================================================================
# Interactive mode: pick STT + LLM + TTS voice, approve, optionally apply.
# =============================================================================
if (( INTERACTIVE )); then
  if [[ ! -t 0 ]]; then
    echo "doctor: -i needs an interactive terminal (stdin is not a TTY)" >&2
    exit 1
  fi

  echo "doctor: interactive model picker"
  echo
  print_hardware_profile
  echo

  # --- STT ------------------------------------------------------------------
  echo "STT — Whisper-MLX variant:"
  i=0
  for entry in "${WHISPER_TABLE[@]}"; do
    i=$((i + 1))
    name="$(echo "$entry" | cut -d'|' -f1)"; disp="$(echo "$entry" | cut -d'|' -f2)"
    mark=""; [[ "$name" == "$DEFAULT_WHISPER" ]] && mark="(default)"
    inst=""; whisper_installed "$(echo "$entry" | cut -d'|' -f4)" && inst="(installed)"
    printf "  %3d) %-18s ~%s GB  %-10s %s\n" "$i" "$name" "$disp" "$mark" "$inst"
  done
  read -r -p "choose STT [default ${DEFAULT_WHISPER}]: " ans || ans=""
  if [[ -z "$ans" ]]; then
    CHOSEN_STT="$DEFAULT_WHISPER"
  elif [[ "$ans" =~ ^[0-9]+$ ]] && (( ans >= 1 && ans <= ${#WHISPER_TABLE[@]} )); then
    CHOSEN_STT="$(echo "${WHISPER_TABLE[$((ans - 1))]}" | cut -d'|' -f1)"
  else
    echo "doctor: '$ans' is not a valid choice" >&2; exit 1
  fi
  STT_GB=0; STT_REPO=""
  for entry in "${WHISPER_TABLE[@]}"; do
    if [[ "${entry%%|*}" == "$CHOSEN_STT" ]]; then
      STT_GB="$(echo "$entry" | cut -d'|' -f3)"
      STT_REPO="$(echo "$entry" | cut -d'|' -f4)"
    fi
  done
  echo

  # --- LLM ------------------------------------------------------------------
  echo "LLM — Ollama model (best fits for this machine first):"
  print_catalog_table numbered
  OLD_IFS="$IFS"; IFS=$'\n'; CATALOG_ROWS=( $(sorted_catalog_rows) ); IFS="$OLD_IFS"
  read -r -p "choose LLM [default ${LLM_MODEL:-qwen2.5:14b}]: " ans || ans=""
  if [[ -z "$ans" ]]; then
    CHOSEN_LLM="${LLM_MODEL:-qwen2.5:14b}"
  elif [[ "$ans" =~ ^[0-9]+$ ]] && (( ans >= 1 && ans <= ${#CATALOG_ROWS[@]} )); then
    CHOSEN_LLM="$(echo "${CATALOG_ROWS[$((ans - 1))]}" | cut -d'|' -f3)"
  else
    CHOSEN_LLM="$ans"  # free-typed tag: allowed, judged by the suffix fallback
  fi
  LLM_GB="$(llm_needs_gb "$CHOSEN_LLM")"
  echo

  # --- TTS voice ------------------------------------------------------------
  echo "TTS — Kokoro voice (model is a constant ~0.3 GB; voice never affects fit):"
  i=0
  for v in "${KOKORO_VOICES[@]}"; do
    i=$((i + 1))
    mark=""; [[ "$v" == "$DEFAULT_VOICE" ]] && mark="(default)"
    printf "  %3d) %-12s %s\n" "$i" "$v" "$mark"
  done
  read -r -p "choose voice [default ${DEFAULT_VOICE}]: " ans || ans=""
  if [[ -z "$ans" ]]; then
    CHOSEN_VOICE="$DEFAULT_VOICE"
  elif [[ "$ans" =~ ^[0-9]+$ ]] && (( ans >= 1 && ans <= ${#KOKORO_VOICES[@]} )); then
    CHOSEN_VOICE="${KOKORO_VOICES[$((ans - 1))]}"
  else
    CHOSEN_VOICE="$ans"  # any Kokoro voice id is fine
  fi
  echo

  # --- Combo verdict --------------------------------------------------------
  echo "doctor: combo check — STT ${CHOSEN_STT} + LLM ${CHOSEN_LLM} + voice ${CHOSEN_VOICE}"
  TOK="$(est_tok_s "$LLM_GB")"
  TOTAL=$(( LLM_GB + STT_GB + 1 ))   # +1 ≈ Kokoro (0.3) rounded up
  SUGGEST="$(sorted_catalog_rows | awk -F'|' '$1==0{print $3; exit}')"
  APPROVED=1
  if (( LLM_GB == 0 )); then
    warn "size of '${CHOSEN_LLM}' unknown — can't judge fit; proceeding on trust"
  elif (( TOTAL + 4 > RAM_GB )); then
    APPROVED=0
    echo "  ❌ REJECTED: needs ~${TOTAL} GB + ~4 GB OS headroom on ${RAM_GB} GB."
    [[ -n "$SUGGEST" ]] && echo "     try ${SUGGEST} instead (best model that fits comfortably)"
  elif (( TOK < 8 )); then
    APPROVED=0
    echo "  ❌ REJECTED: ~${TOK} tok/s estimated — too slow for real-time speech."
    [[ -n "$SUGGEST" ]] && echo "     try ${SUGGEST} instead (best model that fits comfortably)"
  elif (( TOTAL + 6 > RAM_GB )) || (( TOK < 15 )); then
    warn "APPROVED (tight): ~${TOTAL} GB of ${RAM_GB} GB, ~${TOK} tok/s — workable, expect little headroom"
  else
    pass "APPROVED: ~${TOTAL} GB of ${RAM_GB} GB, ~${TOK} tok/s — comfortable"
  fi
  echo
  echo "  .env lines for this combo:"
  echo "     WHISPER_MODEL=${CHOSEN_STT}"
  echo "     LLM_MODEL=${CHOSEN_LLM}"
  echo "     KOKORO_VOICE=${CHOSEN_VOICE}"
  echo
  if (( ! APPROVED )); then
    read -r -p "combo was rejected — continue anyway? [y/N] " ans || ans=""
    [[ "$ans" =~ ^[Yy] ]] || { echo "doctor: no changes made"; exit 1; }
  fi

  # --- Apply: write .env (with backup), only the three model keys -----------
  env_set() {  # KEY VALUE — update in place or append; never touches other lines
    if [[ -f .env ]] && grep -q "^$1=" .env; then
      sed -i '' "s|^$1=.*|$1=$2|" .env
    else
      echo "$1=$2" >> .env
    fi
  }
  read -r -p "write these to .env? (existing .env backed up to .env.bak) [y/N] " ans || ans=""
  if [[ "$ans" =~ ^[Yy] ]]; then
    [[ -f .env ]] && cp .env .env.bak
    env_set WHISPER_MODEL "$CHOSEN_STT"
    env_set LLM_MODEL "$CHOSEN_LLM"
    env_set KOKORO_VOICE "$CHOSEN_VOICE"
    pass "wrote .env (WHISPER_MODEL, LLM_MODEL, KOKORO_VOICE)"
  else
    echo "  skipped — paste the lines above into .env yourself if you want them"
  fi

  # --- Apply: pull whatever is missing (needs network) ----------------------
  NEED_LLM=0;     llm_installed "$CHOSEN_LLM"      || NEED_LLM=1
  NEED_WHISPER=0; whisper_installed "$STT_REPO"    || NEED_WHISPER=1
  if (( NEED_LLM || NEED_WHISPER )); then
    echo
    (( NEED_LLM ))     && echo "  missing: LLM ${CHOSEN_LLM}"
    (( NEED_WHISPER )) && echo "  missing: Whisper ${CHOSEN_STT}"
    read -r -p "pull missing models now? (needs network) [y/N] " ans || ans=""
    if [[ "$ans" =~ ^[Yy] ]]; then
      if (( NEED_LLM )); then
        if ollama list >/dev/null 2>&1; then
          if ! ollama pull "$CHOSEN_LLM"; then
            warn "ollama pull failed — check the tag name and network"
          fi
        else
          warn "no Ollama server running — start ./scripts/run_ollama.sh (it pulls LLM_MODEL from .env on startup)"
        fi
      fi
      if (( NEED_WHISPER )); then
        if command -v uv >/dev/null 2>&1; then
          if ! WHISPER_MODEL="$CHOSEN_STT" uv run python scripts/prefetch_models.py; then
            warn "whisper prefetch failed — retry with: WHISPER_MODEL=${CHOSEN_STT} uv run python scripts/prefetch_models.py"
          fi
        else
          warn "uv not on PATH — install it, then: WHISPER_MODEL=${CHOSEN_STT} uv run python scripts/prefetch_models.py"
        fi
      fi
    else
      echo "  skipped — models will be fetched on first use (needs network then)"
    fi
  fi

  echo
  echo "doctor: ✅ combo ready — ./start.sh"
  exit 0
fi

# =============================================================================
# Default / verbose modes (read-only).
# =============================================================================
echo "doctor: hardware check"
pass "Apple Silicon Mac (${CHIP})"

# --- RAM vs the configured LLM ----------------------------------------------
LLM_MODEL="${LLM_MODEL:-qwen2.5:14b}"

# Best qwen2.5 tag for this much unified memory, leaving headroom for Whisper
# (~1.6 GB), Kokoro (~0.3 GB), and the OS.
if   (( RAM_GB >= 24 )); then RECOMMEND="qwen2.5:14b"
elif (( RAM_GB >= 12 )); then RECOMMEND="qwen2.5:7b"
else                          RECOMMEND="qwen2.5:3b"
fi
pass "${RAM_GB} GB unified memory — recommended LLM: ${RECOMMEND}"

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

# --- Verbose: full capability matrix ----------------------------------------
if (( VERBOSE )); then
  echo
  echo "doctor: hardware profile"
  print_hardware_profile
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

  echo
  echo "doctor: LLM catalog on ${RAM_GB} GB / ~${BW} GB/s (q4; best fits first; ~2 GB STT/TTS + OS headroom)"
  print_catalog_table
  echo "  (pick interactively with ./doctor.sh -i)"

  echo
  echo "doctor: Whisper-MLX variants (all fine on ≥8 GB unless marked)"
  for entry in "${WHISPER_TABLE[@]}"; do
    name="$(echo "$entry" | cut -d'|' -f1)"; disp="$(echo "$entry" | cut -d'|' -f2)"
    verdict="✅"
    [[ "$name" == "LARGE_V3" ]] && (( RAM_GB < 16 )) && verdict="⚠️  tight"
    [[ "$name" == "$DEFAULT_WHISPER" ]] && verdict="$verdict  (default)"
    inst=""; whisper_installed "$(echo "$entry" | cut -d'|' -f4)" && inst="(installed)"
    printf "     %-18s ~%s GB  %-14s %s\n" "$name" "$disp" "$verdict" "$inst"
  done
fi

echo
echo "doctor: ✅ good to go — ./start.sh   (try ./doctor.sh -i to choose different models)"
