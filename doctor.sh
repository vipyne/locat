#!/usr/bin/env bash
#
# doctor.sh — report what this machine can handle for the offline voice bot.
#
# Checks the hard requirement (Apple Silicon for the default Whisper-MLX STT),
# compares total RAM against the configured LLM, and prints recommended
# STT/LLM/TTS cascades sized to this machine. Verbose mode adds a full hardware
# profile (CPU/GPU cores, estimated memory bandwidth, disk) and per-slot model
# catalogs — every STT engine (Whisper-MLX, faster-whisper, Moonshine), the
# curated Ollama LLM catalog ranked by fit (memory footprint AND estimated
# speech latency), and both TTS engines (Kokoro, Piper). Interactive mode walks
# through choosing an engine+model combo, approves it against the hardware, and
# (only with your explicit confirmation) writes it to .env, installs missing
# engine support, and pulls missing models. Without -i it never writes.
#
# Usage:
#   ./doctor.sh          # pass/fail + recommended cascades
#   ./doctor.sh -v       # full capability matrix + STT/LLM/TTS catalogs
#   ./doctor.sh -i       # interactively pick & approve an STT/LLM/TTS combo
#
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"
[[ -f .env ]] && { set -a; source .env; set +a; }

usage() {
  cat <<'EOF'
doctor.sh — report what this machine can handle for the offline voice bot.

Usage:
  ./doctor.sh              hardware check, STT/LLM/TTS cascades sized to this
                           machine, and the currently configured models
  ./doctor.sh -v           the above, plus a full hardware profile (CPU/GPU
                           cores, est. memory bandwidth, disk) and per-slot
                           model catalogs with fit verdicts:
                             STT  Whisper-MLX, faster-whisper, Moonshine
                             LLM  curated Ollama catalog, ranked by memory
                                  footprint AND estimated speech latency
                             TTS  Kokoro, Piper
  ./doctor.sh -i           interactively pick an STT/LLM/TTS combo, get it
                           approved against the hardware, then (each step
                           gated on your confirmation) write it to .env,
                           install missing engine support, and pull missing
                           models

Options:
  -v, --verbose            full capability matrix + model catalogs
  -i, --interactive        guided model picker (the only mode that writes)
  -h, --help               this help

Environment:
  DOCTOR_RAM_GB=<n>        pretend the machine has <n> GB RAM (preview what
                           doctor would say on a smaller Mac)

Without -i, doctor never writes anything.
EOF
}

VERBOSE=0
INTERACTIVE=0
case "${1:-}" in
  -v|--verbose)     VERBOSE=1 ;;
  -i|--interactive) INTERACTIVE=1 ;;
  -h|--help)        usage; exit 0 ;;
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

# STT engine tables. services.py builds whichever engine STT_ENGINE selects.
# Whisper-MLX (Apple GPU): MLXModel member|display GB|GB rounded up|HF repo dir
WHISPER_TABLE=(
  "TINY|0.2|1|whisper-tiny"
  "MEDIUM|1.5|2|whisper-medium-mlx"
  "LARGE_V3|3.0|3|whisper-large-v3-mlx"
  "LARGE_V3_TURBO|1.6|2|whisper-large-v3-turbo"
  "LARGE_V3_TURBO_Q4|0.6|1|whisper-large-v3-turbo-q4"
)
DEFAULT_WHISPER="LARGE_V3_TURBO"

# faster-whisper (CPU): Model member|display GB|GB|HF cache dir|language note
FASTER_WHISPER_TABLE=(
  "TINY|0.1|1|Systran--faster-whisper-tiny|multilingual"
  "BASE|0.15|1|Systran--faster-whisper-base|multilingual"
  "SMALL|0.5|1|Systran--faster-whisper-small|multilingual"
  "MEDIUM|1.5|2|Systran--faster-whisper-medium|multilingual"
  "LARGE|3.0|3|Systran--faster-whisper-large-v3|multilingual"
  "LARGE_V3_TURBO|1.6|2|deepdml--faster-whisper-large-v3-turbo-ct2|multilingual"
  "DISTIL_LARGE_V2|1.5|2|Systran--faster-distil-whisper-large-v2|multilingual"
  "DISTIL_MEDIUM_EN|0.8|1|Systran--faster-distil-whisper-medium.en|English-only (engine default)"
)

# Moonshine (CPU ONNX): Model member|display GB|GB|note
MOONSHINE_TABLE=(
  "TINY|0.1|1|smallest, fastest"
  "BASE|0.2|1|good size/accuracy balance"
  "SMALL_STREAMING|0.4|1|engine default"
  "MEDIUM_STREAMING|1.0|1|largest, most accurate"
)

# TTS voices. Kokoro is one ~0.3 GB model with many voices; Piper voices are
# individual ~60-120 MB models from huggingface.co/rhasspy/piper-voices.
KOKORO_VOICES=(af_heart af_bella af_nicole af_sky am_adam am_michael bf_emma bm_george)
DEFAULT_VOICE="af_heart"
PIPER_VOICES=(en_US-lessac-medium en_US-amy-medium en_US-ryan-high en_GB-alba-medium en_GB-northern_english_male-medium)

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
  echo "  ❌ This is ${OS}/${ARCH} — the bot's default STT (Whisper-MLX) requires an"
  echo "     Apple Silicon Mac; MLX does not run here. STT_ENGINE=faster_whisper"
  echo "     (CPU) is the portable path, but this doctor only profiles macOS."
  echo
  echo "doctor: ❌ this machine cannot run the bot as configured"
  exit 1
fi

# --- Hardware profile (shared by every mode) --------------------------------
CHIP="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo 'Apple Silicon')"
# DOCTOR_RAM_GB overrides detected RAM — preview what fits on a smaller machine,
# e.g.:  DOCTOR_RAM_GB=8 ./doctor.sh -v
RAM_GB="${DOCTOR_RAM_GB:-$(( $(sysctl -n hw.memsize) / 1073741824 ))}"
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

# --- Installed-model / engine-support detection (read-only) -----------------
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

hf_model_installed() {  # $1 = HF cache dir suffix, e.g. mlx-community--whisper-tiny
  [[ -d "${HF_HOME:-$REPO/models/huggingface}/hub/models--$1" ]]
}

piper_voice_installed() {  # $1 = piper voice id
  [[ -f "${PIPER_DOWNLOAD_DIR:-$REPO/models/piper}/$1.onnx" ]]
}

# Optional-engine support: MOONSHINE_OK / PIPER_OK = 1 when the extra's package
# is importable in the venv. One uv invocation, spec lookup only (no imports).
MOONSHINE_OK=0; PIPER_OK=0
probe_engine_support() {
  local out
  out="$(uv run python -c 'import importlib.util as u
print(int(u.find_spec("moonshine_voice") is not None), int(u.find_spec("piper") is not None))' 2>/dev/null || echo "0 0")"
  MOONSHINE_OK="${out%% *}"; PIPER_OK="${out##* }"
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

# Recommended STT+LLM+TTS cascades, computed from the catalog for THIS machine.
# Thinking/reasoning LLMs are excluded — they burn seconds "thinking" before the
# first spoken word — but remain in the catalog for deliberate picking via -i.
print_cascades() {
  local rows quality balanced snappy
  rows="$(sorted_catalog_rows | awk -F'|' '$1==0 && $7 !~ /thinking|reasoning/')"
  [[ -z "$rows" ]] && return 0
  quality="$(echo "$rows" | head -1)"                            # largest that fits ✅
  balanced="$(echo "$rows" | awk -F'|' '$4>=25{print; exit}')"   # largest at ≥25 tok/s
  snappy="$(echo "$rows" | awk -F'|' '$4>=100{print; exit}')"    # largest at ≥100 tok/s

  local q_stt="LARGE_V3_TURBO"; (( RAM_GB >= 16 )) && q_stt="LARGE_V3"
  echo "doctor: recommended cascades for this machine"
  print_cascade_row "balanced"     "$balanced" "LARGE_V3_TURBO"    2
  [[ "$(row_tag "$quality")" != "$(row_tag "$balanced")" ]] \
    && print_cascade_row "best quality" "$quality" "$q_stt" "$( [[ $q_stt == LARGE_V3 ]] && echo 3 || echo 2 )"
  [[ "$(row_tag "$snappy")" != "$(row_tag "$balanced")" ]] \
    && print_cascade_row "snappiest"    "$snappy"  "LARGE_V3_TURBO_Q4" 1
  echo "  (apply one with ./doctor.sh -i)"
}

row_tag() { echo "${1:-}" | cut -d'|' -f3; }

print_cascade_row() {  # tier-name  catalog-row  whisper-name  whisper-int-gb
  local name=$1 row=$2 wname=$3 wgb=$4 tag gb tok wdisp entry
  [[ -z "$row" ]] && return 0
  tag="$(echo "$row" | cut -d'|' -f3)"
  gb="$(echo "$row" | cut -d'|' -f2)"
  tok="$(echo "$row" | cut -d'|' -f4)"
  wdisp=""
  for entry in "${WHISPER_TABLE[@]}"; do
    [[ "${entry%%|*}" == "$wname" ]] && wdisp="$(echo "$entry" | cut -d'|' -f2)"
  done
  printf "  %-13s STT %s ~%sGB · LLM %s ~%sGB ~%stok/s · TTS Kokoro %s   ≈%d GB\n" \
    "$name" "$wname" "$wdisp" "$tag" "$gb" "$tok" "$DEFAULT_VOICE" $(( gb + wgb + 1 ))
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

# Fit verdict for an STT/TTS model of $1 (rounded-up) GB. Same headroom idea as
# llm_verdict, from the other side: the model must coexist with an LLM + OS.
stt_verdict() {
  local gb=$1
  if   (( gb + 6 <= RAM_GB )); then echo "✅ good"
  elif (( gb + 4 <= RAM_GB )); then echo "⚠️  tight"
  else                              echo "❌ too big"
  fi
}

# Print an STT engine group's rows with continuous numbering.
# $1=table-array-name is not portable in bash 3.2, so each group is explicit.
print_whisper_mlx_rows() {  # $1 = "numbered"|"plain"; increments STT_N
  local entry name disp verdict mark inst
  for entry in "${WHISPER_TABLE[@]}"; do
    STT_N=$((STT_N + 1))
    name="$(echo "$entry" | cut -d'|' -f1)"; disp="$(echo "$entry" | cut -d'|' -f2)"
    verdict="$(stt_verdict "$(echo "$entry" | cut -d'|' -f3)")"
    mark=""; [[ "$name" == "$DEFAULT_WHISPER" ]] && mark="(default)"
    inst=""; hf_model_installed "mlx-community--$(echo "$entry" | cut -d'|' -f4)" && inst="(installed)"
    if [[ "$1" == "numbered" ]]; then
      printf "  %3d) %-18s ~%s GB  %-12s %-10s %s\n" "$STT_N" "$name" "$disp" "$verdict" "$mark" "$inst"
    else
      printf "     %-18s ~%s GB  %-12s %-10s %s\n" "$name" "$disp" "$verdict" "$mark" "$inst"
    fi
  done
}

print_faster_whisper_rows() {
  local entry name disp verdict note inst
  for entry in "${FASTER_WHISPER_TABLE[@]}"; do
    STT_N=$((STT_N + 1))
    name="$(echo "$entry" | cut -d'|' -f1)"; disp="$(echo "$entry" | cut -d'|' -f2)"
    verdict="$(stt_verdict "$(echo "$entry" | cut -d'|' -f3)")"
    # The full-size (non-distilled, non-turbo) models transcribe slowly on CPU
    # even when they fit in RAM — a latency problem, not a memory one.
    case "$name" in MEDIUM|LARGE) [[ "$verdict" == "✅ good" ]] && verdict="⚠️  slow on CPU" ;; esac
    note="$(echo "$entry" | cut -d'|' -f5)"
    inst=""; hf_model_installed "$(echo "$entry" | cut -d'|' -f4)" && inst="(installed)"
    if [[ "$1" == "numbered" ]]; then
      printf "  %3d) %-18s ~%s GB  %-16s %-28s %s\n" "$STT_N" "$name" "$disp" "$verdict" "$note" "$inst"
    else
      printf "     %-18s ~%s GB  %-16s %-28s %s\n" "$name" "$disp" "$verdict" "$note" "$inst"
    fi
  done
}

print_moonshine_rows() {
  local entry name disp verdict note
  for entry in "${MOONSHINE_TABLE[@]}"; do
    STT_N=$((STT_N + 1))
    name="$(echo "$entry" | cut -d'|' -f1)"; disp="$(echo "$entry" | cut -d'|' -f2)"
    verdict="$(stt_verdict "$(echo "$entry" | cut -d'|' -f3)")"
    note="$(echo "$entry" | cut -d'|' -f4)"
    if [[ "$1" == "numbered" ]]; then
      printf "  %3d) %-18s ~%s GB  %-12s %s\n" "$STT_N" "$name" "$disp" "$verdict" "$note"
    else
      printf "     %-18s ~%s GB  %-12s %s\n" "$name" "$disp" "$verdict" "$note"
    fi
  done
}

moonshine_hint() { (( MOONSHINE_OK )) || echo " · needs: uv sync --extra moonshine"; }
piper_hint()     { (( PIPER_OK ))     || echo " · needs: uv sync --extra piper"; }

print_stt_groups() {  # $1 = "numbered"|"plain"
  STT_N=0
  echo "  Whisper-MLX (Apple GPU · multilingual)"
  print_whisper_mlx_rows "$1"
  echo "  faster-whisper (CPU · the non-Apple-Silicon path)"
  print_faster_whisper_rows "$1"
  echo "  Moonshine (CPU ONNX · English + a few languages$(moonshine_hint))"
  print_moonshine_rows "$1"
}

print_tts_groups() {  # $1 = "numbered"|"plain"
  local v mark inst
  TTS_N=0
  echo "  Kokoro (ONNX · one ~0.3 GB model, voice is just a setting · $(stt_verdict 1))"
  for v in "${KOKORO_VOICES[@]}"; do
    TTS_N=$((TTS_N + 1))
    mark=""; [[ "$v" == "$DEFAULT_VOICE" ]] && mark="(default)"
    if [[ "$1" == "numbered" ]]; then
      printf "  %3d) %-36s %s\n" "$TTS_N" "$v" "$mark"
    else
      printf "     %-36s %s\n" "$v" "$mark"
    fi
  done
  echo "  Piper (each voice its own ~0.1 GB model · GPL-3.0 · $(stt_verdict 1)$(piper_hint))"
  for v in "${PIPER_VOICES[@]}"; do
    TTS_N=$((TTS_N + 1))
    inst=""; piper_voice_installed "$v" && inst="(installed)"
    if [[ "$1" == "numbered" ]]; then
      printf "  %3d) %-36s %s\n" "$TTS_N" "$v" "$inst"
    else
      printf "     %-36s %s\n" "$v" "$inst"
    fi
  done
}

# Resolve STT pick number $1 -> sets CHOSEN_STT_ENGINE/MODEL/GB/DISP/HFDIR.
resolve_stt_pick() {
  local n=$1 entry
  local w=${#WHISPER_TABLE[@]} f=${#FASTER_WHISPER_TABLE[@]}
  if (( n <= w )); then
    entry="${WHISPER_TABLE[$((n - 1))]}"
    CHOSEN_STT_ENGINE="whisper_mlx"
    CHOSEN_STT_HFDIR="mlx-community--$(echo "$entry" | cut -d'|' -f4)"
  elif (( n <= w + f )); then
    entry="${FASTER_WHISPER_TABLE[$((n - w - 1))]}"
    CHOSEN_STT_ENGINE="faster_whisper"
    CHOSEN_STT_HFDIR="$(echo "$entry" | cut -d'|' -f4)"
  else
    entry="${MOONSHINE_TABLE[$((n - w - f - 1))]}"
    CHOSEN_STT_ENGINE="moonshine"
    CHOSEN_STT_HFDIR=""
  fi
  CHOSEN_STT_MODEL="$(echo "$entry" | cut -d'|' -f1)"
  CHOSEN_STT_DISP="$(echo "$entry" | cut -d'|' -f2)"
  STT_GB="$(echo "$entry" | cut -d'|' -f3)"
}

# =============================================================================
# Interactive mode: pick STT + LLM + TTS, approve, optionally apply.
# =============================================================================
if (( INTERACTIVE )); then
  if [[ ! -t 0 ]]; then
    echo "doctor: -i needs an interactive terminal (stdin is not a TTY)" >&2
    exit 1
  fi

  echo "doctor: interactive model picker"
  echo
  print_hardware_profile
  probe_engine_support
  echo

  # --- STT ------------------------------------------------------------------
  echo "STT — speech-to-text engine + model:"
  print_stt_groups numbered
  STT_TOTAL=$STT_N
  read -r -p "choose STT [default ${DEFAULT_WHISPER}]: " ans || ans=""
  if [[ -z "$ans" ]]; then
    resolve_stt_pick 4  # LARGE_V3_TURBO's position in WHISPER_TABLE
  elif [[ "$ans" =~ ^[0-9]+$ ]] && (( ans >= 1 && ans <= STT_TOTAL )); then
    resolve_stt_pick "$ans"
  else
    echo "doctor: '$ans' is not a valid choice" >&2; exit 1
  fi
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

  # --- TTS ------------------------------------------------------------------
  echo "TTS — text-to-speech engine + voice (all tiny next to the LLM):"
  print_tts_groups numbered
  TTS_TOTAL=$TTS_N
  read -r -p "choose voice [default ${DEFAULT_VOICE}]: " ans || ans=""
  if [[ -z "$ans" ]]; then
    CHOSEN_TTS_ENGINE="kokoro"; CHOSEN_VOICE="$DEFAULT_VOICE"
  elif [[ "$ans" =~ ^[0-9]+$ ]] && (( ans >= 1 && ans <= TTS_TOTAL )); then
    if (( ans <= ${#KOKORO_VOICES[@]} )); then
      CHOSEN_TTS_ENGINE="kokoro"; CHOSEN_VOICE="${KOKORO_VOICES[$((ans - 1))]}"
    else
      CHOSEN_TTS_ENGINE="piper"; CHOSEN_VOICE="${PIPER_VOICES[$((ans - ${#KOKORO_VOICES[@]} - 1))]}"
    fi
  else
    CHOSEN_TTS_ENGINE="kokoro"; CHOSEN_VOICE="$ans"  # any Kokoro voice id
  fi
  echo

  # --- Combo verdict --------------------------------------------------------
  echo "doctor: combo check — STT ${CHOSEN_STT_ENGINE}/${CHOSEN_STT_MODEL} + LLM ${CHOSEN_LLM} + TTS ${CHOSEN_TTS_ENGINE}/${CHOSEN_VOICE}"
  TOK="$(est_tok_s "$LLM_GB")"
  TOTAL=$(( LLM_GB + STT_GB + 1 ))   # +1 ≈ TTS (Kokoro 0.3 / Piper 0.1) rounded up
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

  # Which model var the chosen STT engine reads (see config.py).
  case "$CHOSEN_STT_ENGINE" in
    whisper_mlx)    STT_MODEL_VAR="WHISPER_MODEL" ;;
    faster_whisper) STT_MODEL_VAR="FASTER_WHISPER_MODEL" ;;
    moonshine)      STT_MODEL_VAR="MOONSHINE_MODEL" ;;
  esac
  case "$CHOSEN_TTS_ENGINE" in
    kokoro) TTS_VOICE_VAR="KOKORO_VOICE" ;;
    piper)  TTS_VOICE_VAR="PIPER_VOICE" ;;
  esac
  echo
  echo "  .env lines for this combo:"
  echo "     STT_ENGINE=${CHOSEN_STT_ENGINE}"
  echo "     ${STT_MODEL_VAR}=${CHOSEN_STT_MODEL}"
  echo "     LLM_MODEL=${CHOSEN_LLM}"
  echo "     TTS_ENGINE=${CHOSEN_TTS_ENGINE}"
  echo "     ${TTS_VOICE_VAR}=${CHOSEN_VOICE}"
  echo
  if (( ! APPROVED )); then
    read -r -p "combo was rejected — continue anyway? [y/N] " ans || ans=""
    [[ "$ans" =~ ^[Yy] ]] || { echo "doctor: no changes made"; exit 1; }
  fi

  # --- Apply: write .env (with backup), only the model/engine keys ----------
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
    env_set STT_ENGINE "$CHOSEN_STT_ENGINE"
    env_set "$STT_MODEL_VAR" "$CHOSEN_STT_MODEL"
    env_set LLM_MODEL "$CHOSEN_LLM"
    env_set TTS_ENGINE "$CHOSEN_TTS_ENGINE"
    env_set "$TTS_VOICE_VAR" "$CHOSEN_VOICE"
    pass "wrote .env (STT_ENGINE, ${STT_MODEL_VAR}, LLM_MODEL, TTS_ENGINE, ${TTS_VOICE_VAR})"
  else
    echo "  skipped — paste the lines above into .env yourself if you want them"
  fi

  # --- Apply: install missing engine support (uv sync --extra ...) ----------
  NEED_MOONSHINE=0; NEED_PIPER=0
  [[ "$CHOSEN_STT_ENGINE" == "moonshine" ]] && (( ! MOONSHINE_OK )) && NEED_MOONSHINE=1
  [[ "$CHOSEN_TTS_ENGINE" == "piper" ]]     && (( ! PIPER_OK ))     && NEED_PIPER=1
  if (( NEED_MOONSHINE || NEED_PIPER )); then
    # uv sync removes extras not listed, so pass every extra that is either
    # already present or newly needed — never uninstall the other engine.
    EXTRA_FLAGS=""
    (( MOONSHINE_OK || NEED_MOONSHINE )) && EXTRA_FLAGS="$EXTRA_FLAGS --extra moonshine"
    (( PIPER_OK || NEED_PIPER ))         && EXTRA_FLAGS="$EXTRA_FLAGS --extra piper"
    echo
    (( NEED_MOONSHINE )) && echo "  missing: Moonshine engine support (python package)"
    (( NEED_PIPER ))     && echo "  missing: Piper engine support (python package)"
    read -r -p "install engine support now? (runs: uv sync${EXTRA_FLAGS}) [y/N] " ans || ans=""
    if [[ "$ans" =~ ^[Yy] ]]; then
      # shellcheck disable=SC2086
      if uv sync $EXTRA_FLAGS; then
        pass "engine support installed"
      else
        warn "uv sync failed — run 'uv sync${EXTRA_FLAGS}' manually"
      fi
    else
      echo "  skipped — the bot will exit with the same uv sync command if you use this engine"
    fi
  fi

  # --- Apply: pull whatever is missing (needs network) ----------------------
  NEED_LLM=0; llm_installed "$CHOSEN_LLM" || NEED_LLM=1
  NEED_WHISPER=0
  [[ "$CHOSEN_STT_ENGINE" == "whisper_mlx" ]] && ! hf_model_installed "$CHOSEN_STT_HFDIR" && NEED_WHISPER=1
  if (( NEED_LLM || NEED_WHISPER )); then
    echo
    (( NEED_LLM ))     && echo "  missing: LLM ${CHOSEN_LLM}"
    (( NEED_WHISPER )) && echo "  missing: Whisper-MLX ${CHOSEN_STT_MODEL}"
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
          if ! WHISPER_MODEL="$CHOSEN_STT_MODEL" uv run python scripts/prefetch_models.py; then
            warn "whisper prefetch failed — retry with: WHISPER_MODEL=${CHOSEN_STT_MODEL} uv run python scripts/prefetch_models.py"
          fi
        else
          warn "uv not on PATH — install it, then: WHISPER_MODEL=${CHOSEN_STT_MODEL} uv run python scripts/prefetch_models.py"
        fi
      fi
    else
      echo "  skipped — models will be fetched on first use (needs network then)"
    fi
  fi
  case "$CHOSEN_STT_ENGINE" in
    faster_whisper) hf_model_installed "$CHOSEN_STT_HFDIR" \
      || echo "  note: faster-whisper ${CHOSEN_STT_MODEL} downloads on first use (needs network once)" ;;
    moonshine) echo "  note: Moonshine ${CHOSEN_STT_MODEL} downloads on first use (needs network once)" ;;
  esac
  [[ "$CHOSEN_TTS_ENGINE" == "piper" ]] && ! piper_voice_installed "$CHOSEN_VOICE" \
    && echo "  note: Piper voice ${CHOSEN_VOICE} downloads on first use (needs network once)"

  echo
  echo "doctor: ✅ combo ready — ./start.sh"
  exit 0
fi

# =============================================================================
# Default / verbose modes (read-only).
# =============================================================================
echo "doctor: hardware check"
pass "Apple Silicon Mac (${CHIP})"
pass "${RAM_GB} GB unified memory"

# --- RAM vs the configured LLM (only speak up if something's off) ------------
LLM_MODEL="${LLM_MODEL:-qwen2.5:14b}"

# Best qwen2.5 tag for this much unified memory, leaving headroom for Whisper
# (~1.6 GB), Kokoro (~0.3 GB), and the OS.
if   (( RAM_GB >= 24 )); then RECOMMEND="qwen2.5:14b"
elif (( RAM_GB >= 12 )); then RECOMMEND="qwen2.5:7b"
else                          RECOMMEND="qwen2.5:3b"
fi

NEED="$(llm_needs_gb "$LLM_MODEL")"
if (( NEED == 0 )); then
  warn "configured LLM '${LLM_MODEL}': size unknown — can't judge fit"
elif (( NEED + 4 > RAM_GB )); then
  warn "configured LLM '${LLM_MODEL}' wants ~${NEED} GB + overhead — tight on ${RAM_GB} GB; consider ${RECOMMEND}"
fi

echo
print_cascades

echo
echo "doctor: current model configuration"
uv run python scripts/print_models.py --bare 2>/dev/null \
  || warn "could not resolve models — run 'uv sync'"

# --- Verbose: full capability matrix ----------------------------------------
if (( VERBOSE )); then
  probe_engine_support
  echo
  echo "💻 💻 💻 hardware profile 💻 💻 💻"
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
  echo "~ LLM catalog ~"
  echo
  echo "  Ollama (local server · registry: ollama.com/library)"
  print_catalog_table
  echo

  echo
  echo "~ STT catalog ~"
  echo
  print_stt_groups plain

  echo
  echo "~ TTS catalog ~"
  echo
  print_tts_groups plain
fi
