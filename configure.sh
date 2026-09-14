#!/usr/bin/env bash
#
# configure.sh — report what this machine can handle for the offline voice bot.
#
# Runs on macOS (Apple Silicon or Intel) and Linux; on non-Apple-Silicon
# machines the Apple-GPU STT (Whisper-MLX) is unavailable, so the CPU engines
# (faster-whisper / Moonshine) become the defaults and recommendations.
# Compares total RAM against the configured LLM and prints recommended
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
#   ./configure.sh       # pass/fail + recommended cascades
#   ./configure.sh -v    # full capability matrix + STT/LLM/TTS catalogs
#   ./configure.sh -i    # interactively pick & approve an STT/LLM/TTS combo
#   ./configure.sh huggingface <url> [quant]
#                        # size-check a GGUF repo vs RAM, pull it via Ollama
#
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"
ENV_FILE="${LOCAT_ENV_FILE:-.env}"
[[ -f "$ENV_FILE" ]] && { set -a; source "$ENV_FILE"; set +a; }
# .env first (it may set LOCAT_MODEL_DIR), then resolve the one model directory
# and export HF_HOME / OLLAMA_MODELS / LOCAT_PIPER_DOWNLOAD_DIR / KOKORO_* from it.
LOCAT_REPO_ROOT="$REPO"
# shellcheck source=scripts/model_dir.sh
. "$REPO/scripts/model_dir.sh"

usage() {
  cat <<'EOF'
configure.sh — report what this machine can handle for the offline voice bot.

Usage:
  ./configure.sh           hardware check, STT/LLM/TTS cascades sized to this
                           machine, and the currently configured models
  ./configure.sh huggingface <url|org/repo> [quant]
                           size-check a GGUF repo on Hugging Face against this
                           machine's memory, then pull it into the Ollama
                           store as hf.co/<org>/<repo>:<quant> (default quant
                           Q4_K_M); prints the LOCAT_LLM_MODEL line to use it
                           but never writes .env
  ./configure.sh -v        the above, plus a full hardware profile (CPU/GPU
                           cores, est. memory bandwidth, disk) and per-slot
                           model catalogs with fit verdicts:
                             STT  Whisper-MLX, faster-whisper, Moonshine
                             LLM  curated Ollama catalog, ranked by memory
                                  footprint AND estimated speech latency
                             TTS  Kokoro, Piper
  ./configure.sh -i        interactively pick an STT/LLM/TTS combo, get it
                           approved against the hardware, then (each step
                           gated on your confirmation) write it to .env,
                           install missing engine support, and pull missing
                           models

Options:
  -v, --verbose            full capability matrix + model catalogs
  -i, --interactive        guided model picker (the only mode that writes)
  -h, --help               this help

The untrimmed catalogs (every model, including ones too big for this machine)
moved to: ./locat.sh status -v

Environment:
  LOCAT_CONFIGURE_RAM_GB=<n>  pretend the machine has <n> GB RAM (preview what
                           configure would say on a smaller machine)
  LOCAT_CATALOG_MAX_AGE_DAYS=<n>
                           refetch the ollama.com catalog when the cache is
                           older than <n> days (default 7; 0 never fetches)
  LOCAT_CATALOG_LIMIT=<n>  LLM rows to keep, best-fitting first (default 60)

Runs on macOS (Apple Silicon or Intel) and Linux. Whisper-MLX needs Apple
Silicon; elsewhere the CPU engines (faster-whisper/Moonshine) are the
defaults. On Windows, run under WSL.

Without -i, configure never writes config; the huggingface subcommand downloads
into the Ollama model store but touches nothing else.
EOF
}

VERBOSE=0
INTERACTIVE=0
SHOW_ALL=0
HARDWARE_ONLY=0
CATALOGS_ONLY=0
HF_CMD=0; HF_REPO=""; HF_QUANT="Q4_K_M"
if [[ "${1:-}" == "huggingface" ]]; then
  HF_CMD=1
  HF_REPO="${2:-}"
  [[ -n "$HF_REPO" ]] || { echo "configure: huggingface needs a model URL or org/repo (try ./configure.sh -h)" >&2; exit 1; }
  [[ $# -le 3 ]] || { echo "configure: too many arguments for huggingface (try ./configure.sh -h)" >&2; exit 1; }
  HF_QUANT="$(printf '%s' "${3:-Q4_K_M}" | tr '[:lower:]' '[:upper:]')"
  shift $#
fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    -v|--verbose)     VERBOSE=1 ;;
    -i|--interactive) INTERACTIVE=1 ;;
    # Plumbing for ./locat.sh status — print one section and exit.
    --hardware)       HARDWARE_ONLY=1 ;;
    --catalogs)       CATALOGS_ONLY=1; SHOW_ALL=1 ;;
    -h|--help)        usage; exit 0 ;;
    "")               ;;
    *) echo "configure: unknown option '$1' (try ./configure.sh -h)" >&2; exit 1 ;;
  esac
  shift
done

pass() { echo "  ✅ $*"; }
warn() { echo "  ⚠️  $*"; }

# --- Model data --------------------------------------------------------------
# ollama.com is a website, not an API, so scripts/fetch_catalog.py scrapes it
# into a cache that load_catalog() reads over the seed array below. The seed is
# the offline fallback — a fresh clone with no network still gets a usable list.
# tag|~GB in memory (q4)|note|active GB (MoE only — speed uses this, RAM uses ~GB)
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
  "nemotron-3-nano:4b|3|"
  "nemotron-3-nano:30b|25|MoE, 3B active|3"
  "nemotron-3.5-lightning:30b|26|MoE, 3B active|3"
  "nemotron-cascade-2:30b|25|MoE, 3B active|3"
  "nemotron:70b|43|"
  "nemotron-3-super:120b|87|MoE, 12B active|12"
)

# STT engine tables. services.py builds whichever engine LOCAT_STT_ENGINE selects.
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
# GB streamed per token for a tag: the catalog's active field when set (MoE),
# otherwise its total size. Free-typed tags fall back to llm_needs_gb.
llm_active_gb() {
  local entry act
  for entry in "${LLM_CATALOG[@]}"; do
    if [[ "${entry%%|*}" == "$1" ]]; then
      act="$(echo "$entry" | cut -d'|' -f4)"
      [[ -n "$act" ]] && { echo "$act"; return; }
      break
    fi
  done
  llm_needs_gb "$1"
}

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

# --- Platform ----------------------------------------------------------------
# Whisper-MLX (Apple GPU) needs an Apple Silicon Mac; every other platform runs
# the CPU engines (faster-whisper / Moonshine). Native Windows isn't supported
# yet — under WSL this takes the Linux path.
OS="$(uname -s)"; ARCH="$(uname -m)"
MLX_OK=0
# LOCAT_CONFIGURE_PLATFORM overrides detection for previewing/testing another platform's
# behavior, e.g.:  LOCAT_CONFIGURE_PLATFORM=Darwin/x86_64 ./configure.sh -v
EFFECTIVE_PLATFORM="${LOCAT_CONFIGURE_PLATFORM:-${OS}/${ARCH}}"
case "$EFFECTIVE_PLATFORM" in
  Darwin/arm64) PLATFORM="Apple Silicon Mac"; MLX_OK=1 ;;
  Darwin/*)     PLATFORM="Intel Mac" ;;
  Linux/*)      PLATFORM="Linux ${EFFECTIVE_PLATFORM#*/}" ;;
  *)            echo "configure: unsupported platform ${EFFECTIVE_PLATFORM} (on Windows, run this under WSL)" >&2
                exit 1 ;;
esac

# --- Hardware profile (shared by every mode) --------------------------------
if [[ "$OS" == "Darwin" ]]; then
  CHIP="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo 'unknown CPU')"
  DETECTED_RAM=$(( $(sysctl -n hw.memsize) / 1073741824 ))
  GPU_CORES="$(system_profiler SPDisplaysDataType 2>/dev/null \
    | awk -F': ' '/Total Number of Cores/{print $2; exit}' || :)"
  CPU_CORES="$(sysctl -n hw.physicalcpu 2>/dev/null || :)"
  CPU_PERF="$(sysctl -n hw.perflevel0.physicalcpu 2>/dev/null || :)"
  CPU_EFF="$(sysctl -n hw.perflevel1.physicalcpu 2>/dev/null || :)"
  OS_VER_LABEL="macOS"; OS_VER="$(sw_vers -productVersion 2>/dev/null || :)"
else
  CHIP="$(awk -F': ' '/^model name/{print $2; exit}' /proc/cpuinfo 2>/dev/null || echo 'unknown CPU')"
  DETECTED_RAM=$(( $(awk '/^MemTotal/{print $2; exit}' /proc/meminfo 2>/dev/null || echo 0) / 1048576 ))
  GPU_CORES=""
  CPU_CORES="$(getconf _NPROCESSORS_ONLN 2>/dev/null || :)"
  CPU_PERF=""; CPU_EFF=""
  OS_VER_LABEL="kernel"; OS_VER="$(uname -r)"
fi
# LOCAT_CONFIGURE_RAM_GB overrides detected RAM — preview what fits on a smaller machine,
# e.g.:  LOCAT_CONFIGURE_RAM_GB=8 ./configure.sh -v
RAM_GB="${LOCAT_CONFIGURE_RAM_GB:-$DETECTED_RAM}"
FREE_DISK="$(df -h . | awk 'NR==2{print $4}')"

# =============================================================================
# huggingface subcommand: size-check a GGUF repo against this machine's RAM,
# then pull it through Ollama (hf.co/<org>/<repo>:<quant>). Ollama only runs
# GGUF, so source-weight repos are rejected with a pointer to community
# conversions instead of downloading something nothing here can serve.
# =============================================================================
if (( HF_CMD )); then
  HF_REPO="${HF_REPO#http://}"; HF_REPO="${HF_REPO#https://}"
  HF_REPO="${HF_REPO#huggingface.co/}"; HF_REPO="${HF_REPO#hf.co/}"
  HF_REPO="${HF_REPO%%\?*}"
  case "$HF_REPO" in
    */tree/*)    HF_REPO="${HF_REPO%%/tree/*}" ;;
    */blob/*)    HF_REPO="${HF_REPO%%/blob/*}" ;;
    */resolve/*) HF_REPO="${HF_REPO%%/resolve/*}" ;;
  esac
  HF_REPO="${HF_REPO%/}"
  if [[ ! "$HF_REPO" =~ ^[^/]+/[^/]+$ ]]; then
    echo "configure: '$HF_REPO' doesn't look like a Hugging Face model URL or org/repo" >&2
    exit 1
  fi

  echo "configure: huggingface ${HF_REPO} (quant ${HF_QUANT})"
  # The tree API lists every file with its size — enough to find the chosen
  # quant (summing split multi-part GGUFs) or report what the repo does have.
  PROBE_STATUS=0
  PROBE="$(python3 - "$HF_REPO" "$HF_QUANT" <<'PY'
import json, re, sys, urllib.request
repo, quant = sys.argv[1], sys.argv[2]
url = f"https://huggingface.co/api/models/{repo}/tree/main"
try:
    with urllib.request.urlopen(url, timeout=15) as r:
        files = json.load(r)
except Exception:
    sys.exit(3)
ggufs = [f for f in files if f.get("path", "").lower().endswith(".gguf")]
if not ggufs:
    print("NOGGUF")
    sys.exit(0)
def quant_of(path):
    name = path.rsplit("/", 1)[-1]
    m = re.search(r"(?i)(?:^|[^a-z0-9])(i?q\d[a-z0-9_]*)", name)
    return m.group(1).upper() if m else ""
total = sum(f.get("size", 0) for f in ggufs if quant_of(f["path"]) == quant)
if total == 0:
    avail = sorted({q for q in (quant_of(f["path"]) for f in ggufs) if q})
    print("NOQUANT " + ",".join(avail))
    sys.exit(0)
print(f"SIZE {-(-total // 2**30)}")
PY
)" || PROBE_STATUS=$?

  MODEL_GB=""
  if (( PROBE_STATUS != 0 )); then
    warn "couldn't reach huggingface.co to size the model — offline?"
  elif [[ "$PROBE" == "NOGGUF" ]]; then
    echo "  ❌ ${HF_REPO} has no GGUF files — locat serves LLMs through Ollama, which"
    echo "     only runs GGUF. Source-weight repos usually have community conversions:"
    echo "       https://huggingface.co/models?search=${HF_REPO#*/}+gguf"
    exit 1
  elif [[ "$PROBE" == NOQUANT* ]]; then
    echo "  ❌ no ${HF_QUANT} file in ${HF_REPO}; available: ${PROBE#NOQUANT }" >&2
    exit 1
  else
    MODEL_GB="${PROBE#SIZE }"
    echo "  ${HF_QUANT} is ~${MODEL_GB} GB on disk; this machine has ${RAM_GB} GB RAM"
  fi

  warn "no fit check on arbitrary models — we don't know whether this will run well on this machine, or at all"
  if [[ -z "$MODEL_GB" ]]; then
    read -r -p "size unknown — download anyway? [y/N] " ans || ans=""
    [[ "$ans" =~ ^[Yy] ]] || { echo "configure: nothing downloaded"; exit 1; }
  elif (( MODEL_GB > RAM_GB )); then
    warn "model is LARGER than this machine's memory (~${MODEL_GB} GB vs ${RAM_GB} GB RAM)"
    read -r -p "download anyway? [y/N] " ans || ans=""
    [[ "$ans" =~ ^[Yy] ]] || { echo "configure: nothing downloaded"; exit 1; }
  fi

  if ! ollama list >/dev/null 2>&1; then
    warn "no Ollama server running — start ./scripts/run_ollama.sh, then rerun this"
    exit 1
  fi
  HF_TAG="hf.co/${HF_REPO}:${HF_QUANT}"
  if ! ollama pull "$HF_TAG"; then
    warn "ollama pull failed — check the repo name, quant, and network"
    exit 1
  fi
  pass "pulled ${HF_TAG}"
  echo "  to use it, set in .env (configure won't write it for you):"
  echo "     LOCAT_LLM_MODEL=${HF_TAG}"
  exit 0
fi

# --- Live LLM catalog --------------------------------------------------------
# ollama's library changes constantly, so the catalog is fetched and cached
# rather than hand-maintained. Refresh is weekly and only on the modes that
# actually show the catalog — never in the background, never on a bare run.
CATALOG_CACHE="${LOCAT_MODEL_DIR}/.llm-catalog"
CATALOG_MAX_AGE="${LOCAT_CATALOG_MAX_AGE_DAYS:-7}"   # 0 disables fetching entirely
CATALOG_LIMIT="${LOCAT_CATALOG_LIMIT:-60}"           # rows kept, most-pulled first
CATALOG_STATUS=""

refresh_catalog() {
  (( CATALOG_MAX_AGE == 0 )) && return 0
  command -v python3 >/dev/null 2>&1 || return 0
  # -mtime +N is "older than N+1 days", so subtract one to mean "older than AGE".
  if [[ -s "$CATALOG_CACHE" ]] \
     && [[ -z "$(find "$CATALOG_CACHE" -mtime +$(( CATALOG_MAX_AGE - 1 )) 2>/dev/null)" ]]; then
    return 0
  fi
  echo "configure: refreshing LLM catalog from ollama.com (every ${CATALOG_MAX_AGE} days)…"
  local tmp; tmp="$(mktemp)"
  if python3 "$REPO/scripts/fetch_catalog.py" >"$tmp" 2>/dev/null && [[ -s "$tmp" ]]; then
    mkdir -p "$(dirname "$CATALOG_CACHE")" && mv "$tmp" "$CATALOG_CACHE"
  else
    rm -f "$tmp"
    warn "catalog refresh failed (offline?) — using what we already have"
  fi
}

# Replace the seed array with the cache, keeping the most-pulled models that fit
# this machine. --all keeps everything, including models too big to run.
load_catalog() {
  [[ -s "$CATALOG_CACHE" ]] || { CATALOG_STATUS="built-in list"; return 0; }
  local line gb fam seen_fams="" kept=() n=0 configured="${LOCAT_LLM_MODEL:-}"
  while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    gb="$(echo "$line" | cut -d'|' -f2)"
    [[ "$gb" =~ ^[0-9]+$ ]] || continue
    if (( SHOW_ALL == 0 )); then
      (( gb + 4 > RAM_GB )) && continue
      (( n >= CATALOG_LIMIT )) && continue
      # Cap size variants per family. Without this, a handful of popular
      # families spend the whole budget on their own 7 sizes and the list
      # shows ~11 distinct models. Cache order is largest-first, so the
      # survivors are the biggest that still fit.
      fam="${line%%:*}"
      case " $seen_fams " in
        *" $fam:$fam "*) continue ;;
        *" $fam "*) seen_fams="$seen_fams $fam:$fam" ;;
        *) seen_fams="$seen_fams $fam" ;;
      esac
    fi
    kept+=("$line"); n=$(( n + 1 ))
  done < "$CATALOG_CACHE"
  (( ${#kept[@]} )) || { CATALOG_STATUS="built-in list"; return 0; }
  # Whatever is configured must be judgeable even if it missed the popularity cut.
  if [[ -n "$configured" ]] && ! printf '%s\n' "${kept[@]}" | grep -q "^${configured}|"; then
    line="$(grep -m1 "^${configured}|" "$CATALOG_CACHE" 2>/dev/null || :)"
    [[ -n "$line" ]] && kept+=("$line")
  fi
  LLM_CATALOG=( "${kept[@]}" )
  local days
  days="$(( ( $(date +%s) - $(catalog_mtime) ) / 86400 ))"
  CATALOG_STATUS="ollama.com, $( (( days <= 0 )) && echo "fetched today" || echo "${days}d old" )"
}

catalog_mtime() { stat -f %m "$CATALOG_CACHE" 2>/dev/null || stat -c %Y "$CATALOG_CACHE" 2>/dev/null || echo 0; }

if (( VERBOSE || INTERACTIVE || CATALOGS_ONLY )); then refresh_catalog; fi
load_catalog

# Memory bandwidth (GB/s) — decode speed of a q4 LLM is bandwidth-bound, so
# this single number predicts tokens/sec. Apple Silicon's unified memory is
# well documented per chip; elsewhere assume dual-channel DDR-class bandwidth
# (CPU decode is still bandwidth-bound, so the estimate stays meaningful).
BW_EST=""  # non-empty when we fell back to a guess
if (( MLX_OK )); then
  case "$CHIP" in
    *M1\ Ultra*) BW=800 ;; *M1\ Max*) BW=400 ;; *M1\ Pro*) BW=200 ;; *M1*) BW=68  ;;
    *M2\ Ultra*) BW=800 ;; *M2\ Max*) BW=400 ;; *M2\ Pro*) BW=200 ;; *M2*) BW=100 ;;
    *M3\ Ultra*) BW=800 ;; *M3\ Max*) BW=400 ;; *M3\ Pro*) BW=150 ;; *M3*) BW=100 ;;
                          *M4\ Max*) BW=546 ;; *M4\ Pro*) BW=273 ;; *M4*) BW=120 ;;
    *) BW=100; BW_EST="unrecognized chip — assuming" ;;
  esac
else
  BW=40; BW_EST="non-unified memory — assuming"
fi

# The -i default STT pick (menu numbers run continuously across engine groups):
# Whisper-MLX LARGE_V3_TURBO on Apple Silicon, else faster-whisper's default.
if (( MLX_OK )); then
  DEFAULT_STT_NAME="$DEFAULT_WHISPER"; DEFAULT_STT_PICK=0
  _i=0; for _e in "${WHISPER_TABLE[@]}"; do _i=$((_i + 1))
    [[ "${_e%%|*}" == "$DEFAULT_WHISPER" ]] && DEFAULT_STT_PICK=$_i
  done
else
  DEFAULT_STT_NAME="DISTIL_MEDIUM_EN"; DEFAULT_STT_PICK=0
  _i=${#WHISPER_TABLE[@]}; for _e in "${FASTER_WHISPER_TABLE[@]}"; do _i=$((_i + 1))
    [[ "${_e%%|*}" == "$DEFAULT_STT_NAME" ]] && DEFAULT_STT_PICK=$_i
  done
fi
unset _i _e

# Rough decode speed for a q4 model of $1 GB on this chip's bandwidth.
# $1 = the GB actually streamed per token. For dense models that is the whole
# model; for MoE it is only the active experts, so pass the catalog's 4th field.
est_tok_s() { echo $(( BW / ( $1 > 0 ? $1 : 1 ) )); }

# Fit verdict for an LLM of $1 GB: prints "rank|verdict" (rank sorts: 0 best).
# RAM headroom mirrors the combo math: ~2 GB STT/TTS + ~4 GB OS.
llm_verdict() {  # $1 = total GB (RAM), $2 = active GB (speed; defaults to $1)
  local gb=$1 tok; tok="$(est_tok_s "${2:-$1}")"
  if   (( gb + 4 > RAM_GB )); then echo "3|❌ too big"
  elif (( tok < 8 ));          then echo "2|🐢 too slow for voice"
  elif (( gb + 6 > RAM_GB ));  then echo "1|⚠️  tight fit"
  elif (( tok < 15 ));         then echo "1|⚠️  sluggish"
  else                              echo "0|✅ good"
  fi
}

# --- Installed-model / engine-support detection (read-only) -----------------
# OLLAMA_MODELS / HF_HOME / LOCAT_PIPER_DOWNLOAD_DIR are already resolved and exported
# by scripts/model_dir.sh at the top of this file.
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

# The embed model is usually configured bare ("nomic-embed-text") while the
# store tags it ":latest" — match either form, like rag.py's preflight does.
EMBED_MODEL="${LOCAT_EMBED_MODEL:-nomic-embed-text}"
embed_installed() {
  local t
  for t in $INSTALLED_TAGS; do
    [[ "$t" == "$EMBED_MODEL" || "${t%%:*}" == "$EMBED_MODEL" ]] && return 0
  done
  return 1
}

# Installed models the catalog has no row for (hf.co/... GGUF pulls, custom
# builds) still deserve fit verdicts: append them, sized from what is actually
# on disk — `ollama list` when a server answers, else the store's manifests.
append_installed_rows() {
  local pairs="" tag gb entry dup
  if command -v ollama >/dev/null 2>&1 && ollama list >/dev/null 2>&1; then
    pairs="$(ollama list 2>/dev/null | awk 'NR>1 && NF>=4 {
      n=$3; gb=($4=="GB") ? int(n)+(n>int(n)) : 1; print $1"|"gb }')"
  elif [[ -d "$OLLAMA_MODELS/manifests" ]] && command -v python3 >/dev/null 2>&1; then
    pairs="$(python3 - "$OLLAMA_MODELS/manifests" <<'PY'
import json, os, sys
root = sys.argv[1]
for dirpath, _, files in os.walk(root):
    for f in files:
        path = os.path.join(dirpath, f)
        rel = os.path.relpath(path, root).split(os.sep)
        if len(rel) < 3:
            continue
        host, mid, tagname = rel[0], rel[1:-1], rel[-1]
        if host == "registry.ollama.ai":
            if mid and mid[0] == "library":
                mid = mid[1:]
            tag = "/".join(mid) + ":" + tagname
        else:
            tag = host + "/" + "/".join(mid) + ":" + tagname
        try:
            with open(path) as fh:
                total = sum(l.get("size", 0) for l in json.load(fh).get("layers", []))
        except (OSError, ValueError):
            continue
        # Decimal GB, matching `ollama list`, fetch_catalog.py and the seed
        # array — every size in this column must use the same ruler.
        print(f"{tag}|{max(1, round(total / 1e9))}")
PY
)"
  fi
  [[ -z "$pairs" ]] && return 0
  while IFS='|' read -r tag gb; do
    [[ -z "$tag" || -z "$gb" ]] && continue
    # Embedding models aren't conversation LLMs — keep them out of the catalog.
    [[ "${tag%%:*}" == "${EMBED_MODEL%%:*}" || "$tag" == *embed* ]] && continue
    dup=0
    for entry in "${LLM_CATALOG[@]}"; do
      [[ "${entry%%|*}" == "$tag" ]] && { dup=1; break; }
    done
    (( dup )) && continue
    LLM_CATALOG+=("${tag}|${gb}")
  done <<<"$pairs"
}
append_installed_rows

hf_model_installed() {  # $1 = HF cache dir suffix, e.g. mlx-community--whisper-tiny
  [[ -d "${HF_HOME}/hub/models--$1" ]]
}

piper_voice_installed() {  # $1 = piper voice id
  [[ -f "${LOCAT_PIPER_DOWNLOAD_DIR}/$1.onnx" ]]
}

# Optional-extra support: *_OK = 1 when the extra's package is importable in the
# venv. One uv invocation, spec lookup only (no imports). LOCAL_AUDIO_OK covers
# the `local-audio` extra (pyaudio, needed only by bot.py); it is tracked here
# because `uv sync` uninstalls any extra not passed on the command line, so the
# -i flow has to re-list it to avoid silently removing it.
MOONSHINE_OK=0; PIPER_OK=0; LOCAL_AUDIO_OK=0
probe_engine_support() {
  local out
  out="$(uv run python -c 'import importlib.util as u
for m in ("moonshine_voice", "piper", "pyaudio"):
    print(int(u.find_spec(m) is not None), end=" ")' 2>/dev/null || echo "0 0 0")"
  read -r MOONSHINE_OK PIPER_OK LOCAL_AUDIO_OK <<<"$out"
}

# Catalog rows, best-fitting first: "rank|gb|tag|tok/s|verdict|installed|note"
sorted_catalog_rows() {
  local entry tag gb note act tok rv rank verdict inst
  for entry in "${LLM_CATALOG[@]}"; do
    tag="$(echo "$entry" | cut -d'|' -f1)"
    gb="$(echo "$entry"  | cut -d'|' -f2)"
    note="$(echo "$entry" | cut -d'|' -f3)"
    act="$(echo "$entry" | cut -d'|' -f4)"; act="${act:-$gb}"
    tok="$(est_tok_s "$act")"
    rv="$(llm_verdict "$gb" "$act")"; rank="${rv%%|*}"; verdict="${rv#*|}"
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
    printf "  %s %-26s ~%2d GB  ~%3d tok/s  %-22s %-12s %s\n" \
      "$prefix" "$tag" "$gb" "$tok" "$verdict" "$inst" "$note"
    (( i % 10 == 0 )) && echo
    IFS=$'\n'
  done
  IFS="$OLD_IFS"
}

# Recommended STT+LLM+TTS cascades, computed from the catalog for THIS machine.
# Thinking/reasoning LLMs are excluded — they burn seconds "thinking" before the
# first spoken word — but remain in the catalog for deliberate picking via -i.
# The recommended cascades, computed once into parallel arrays (bash 3.2 has
# no associative arrays). Model numbers in all cascade output match the
# models' positions in the -i menus, so a recommendation can be applied by
# just typing the same numbers there.
CAS_NAMES=(); CAS_ROWS=(); CAS_WNAMES=(); CAS_WGBS=()

compute_cascades() {
  (( ${#CAS_NAMES[@]} )) && return 0  # already computed
  local rows quality balanced snappy bal_stt bal_gb q_stt q_gb sn_stt sn_gb
  # Note-based filter first; the tag check also catches reasoning-model families
  # merged from `ollama list`, whose rows carry no note (e.g. an installed deepseek-r1).
  rows="$(sorted_catalog_rows \
    | awk -F'|' '$1==0 && $7 !~ /thinking|reasoning/ && $3 !~ /^(deepseek-r1|qwq|magistral|phi4-reasoning|openthinker)/')"
  [[ -z "$rows" ]] && return 0
  # Three DISTINCT tiers, even when one model tops every metric (MoE models are
  # both the largest and among the fastest, which used to collapse all three):
  # quality = largest that fits; snappy = fastest other model (ties → larger);
  # balanced = largest remaining at ≥25 tok/s, else the largest remaining.
  quality="$(echo "$rows" | head -1)"
  local qtag stag
  qtag="$(row_tag "$quality")"
  snappy="$(echo "$rows" | sort -t'|' -k4,4rn -k2,2rn \
    | awk -F'|' -v q="$qtag" '$3!=q{print; exit}')"
  stag="$(row_tag "$snappy")"
  balanced="$(echo "$rows" \
    | awk -F'|' -v q="$qtag" -v s="$stag" '$4>=25 && $3!=q && $3!=s {print; exit}')"
  [[ -z "$balanced" ]] && balanced="$(echo "$rows" \
    | awk -F'|' -v q="$qtag" -v s="$stag" '$3!=q && $3!=s {print; exit}')"

  if (( MLX_OK )); then
    bal_stt="LARGE_V3_TURBO"; bal_gb=2
    q_stt="LARGE_V3_TURBO"; q_gb=2
    (( RAM_GB >= 16 )) && { q_stt="LARGE_V3"; q_gb=3; }
    sn_stt="LARGE_V3_TURBO_Q4"; sn_gb=1
  else
    # CPU transcription: the distilled models keep latency voice-usable.
    bal_stt="DISTIL_MEDIUM_EN"; bal_gb=1
    q_stt="DISTIL_LARGE_V2";    q_gb=2
    sn_stt="BASE";              sn_gb=1
  fi
  add_cascade "balanced"     "$balanced" "$bal_stt" "$bal_gb"
  add_cascade "best quality" "$quality"  "$q_stt"   "$q_gb"
  add_cascade "snappiest"    "$snappy"   "$sn_stt"  "$sn_gb"
  return 0
}

add_cascade() {  # tier-name  catalog-row  whisper-name  whisper-int-gb
  [[ -z "$2" ]] && return 0
  CAS_NAMES+=("$1"); CAS_ROWS+=("$2"); CAS_WNAMES+=("$3"); CAS_WGBS+=("$4")
}

row_tag() { echo "${1:-}" | cut -d'|' -f3; }

whisper_lookup() {  # $1 = STT model name → sets WNUM (menu number) + WDISP (~GB)
  # Cascades recommend Whisper-MLX models on Apple Silicon and faster-whisper
  # models elsewhere; menu numbers continue across the engine groups, so the
  # faster-whisper search starts past the Whisper-MLX rows. Both tables reuse
  # names (TINY, LARGE_V3_TURBO, …) — the platform picks which table applies.
  local entry i=0; WNUM=0; WDISP=""
  (( MLX_OK )) || i=${#WHISPER_TABLE[@]}
  if (( MLX_OK )); then
    for entry in "${WHISPER_TABLE[@]}"; do
      i=$((i + 1))
      [[ "${entry%%|*}" == "$1" ]] && { WNUM=$i; WDISP="$(echo "$entry" | cut -d'|' -f2)"; }
    done
  else
    for entry in "${FASTER_WHISPER_TABLE[@]}"; do
      i=$((i + 1))
      [[ "${entry%%|*}" == "$1" ]] && { WNUM=$i; WDISP="$(echo "$entry" | cut -d'|' -f2)"; }
    done
  fi
  return 0
}

llm_menu_num() { sorted_catalog_rows | awk -F'|' -v t="$1" '$3==t{print NR; exit}'; }

kokoro_menu_num() {  # voice id → its number in the -i TTS menu (Kokoro is listed first)
  local v i=0
  for v in "${KOKORO_VOICES[@]}"; do
    i=$((i + 1))
    [[ "$v" == "$1" ]] && { echo "$i"; return 0; }
  done
  echo 1
}

cascade_stt_line() {  # $1 = tier index
  whisper_lookup "${CAS_WNAMES[$1]}"
  printf "%2d) %s ~%sGB" "$WNUM" "${CAS_WNAMES[$1]}" "$WDISP"
}

cascade_llm_line() {  # $1 = tier index
  local row="${CAS_ROWS[$1]}" tag gb tok
  tag="$(echo "$row" | cut -d'|' -f3)"
  gb="$(echo "$row" | cut -d'|' -f2)"
  tok="$(echo "$row" | cut -d'|' -f4)"
  printf "%2d) %s ~%sGB ~%stok/s" "$(llm_menu_num "$tag")" "$tag" "$gb" "$tok"
}

cascade_total_gb() {  # $1 = tier index → ≈GB for the whole cascade
  local gb; gb="$(echo "${CAS_ROWS[$1]}" | cut -d'|' -f2)"
  echo $(( gb + CAS_WGBS[$1] + 1 ))
}

print_cascades() {
  compute_cascades
  (( ${#CAS_NAMES[@]} )) || return 0
  local i
  echo "configure: recommended cascades for this machine"
  for i in $(seq 0 $(( ${#CAS_NAMES[@]} - 1 ))); do
    echo "  ${CAS_NAMES[$i]}"
    echo "    STT $(cascade_stt_line "$i")"
    echo "    LLM $(cascade_llm_line "$i")"
    printf "    TTS %2d) Kokoro %s   ≈%d GB\n" \
      "$(kokoro_menu_num "$DEFAULT_VOICE")" "$DEFAULT_VOICE" "$(cascade_total_gb "$i")"
  done
  # No point advertising -i to someone already running it.
  (( INTERACTIVE )) || echo "  (apply one with ./configure.sh -i)"
}

print_slot_reccos() {  # $1 = stt|llm|tts — reprint one slot's recommendations above its -i menu
  compute_cascades
  (( ${#CAS_NAMES[@]} )) || return 0
  local i
  echo
  if [[ "$1" == "tts" ]]; then
    # Every cascade recommends the default voice, so one line covers them all.
    printf "  recommended:   %2d) Kokoro %s   (all cascades)\n" \
      "$(kokoro_menu_num "$DEFAULT_VOICE")" "$DEFAULT_VOICE"
    echo
    return 0
  fi
  echo "  recommended:"
  for i in $(seq 0 $(( ${#CAS_NAMES[@]} - 1 ))); do
    case "$1" in
      stt) printf "    %-13s %s\n" "${CAS_NAMES[$i]}" "$(cascade_stt_line "$i")" ;;
      llm) printf "    %-13s %s\n" "${CAS_NAMES[$i]}" "$(cascade_llm_line "$i")" ;;
    esac
  done
  echo
  return 0
}

print_hardware_profile() {
  printf "  %-12s%s\n" "chip:" "${CHIP}"
  printf "  %-12s%s\n" "${OS_VER_LABEL}:" "${OS_VER:-unknown}"
  printf "  %-12s%s\n" "memory:" "${RAM_GB} GB$( (( MLX_OK )) && echo ' unified' )"
  if [[ -n "$CPU_PERF" && -n "$CPU_EFF" ]]; then
    printf "  %-12s%s\n" "CPU cores:" "${CPU_CORES:-?} (${CPU_PERF} performance + ${CPU_EFF} efficiency)"
  else
    printf "  %-12s%s\n" "CPU cores:" "${CPU_CORES:-unknown}"
  fi
  [[ -n "$GPU_CORES" || "$OS" == "Darwin" ]] \
    && printf "  %-12s%s\n" "GPU cores:" "${GPU_CORES:-unknown}"
  printf "  %-12s%s\n" "memory bw:" "~${BW} GB/s ${BW_EST:+(${BW_EST} baseline) }(est. — governs LLM tokens/sec)"
  printf "  %-12s%s\n" "free disk:" "${FREE_DISK} available on this volume"
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
    if (( MLX_OK )); then
      verdict="$(stt_verdict "$(echo "$entry" | cut -d'|' -f3)")"
    else
      verdict="❌ needs Apple Silicon"
    fi
    mark=""; [[ "$name" == "$DEFAULT_WHISPER" ]] && (( MLX_OK )) && mark="(default)"
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
    [[ "$name" == "$DEFAULT_STT_NAME" ]] && (( ! MLX_OK )) && note="${note} (default)"
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
  echo "  Whisper-MLX (Apple GPU · multilingual$( (( MLX_OK )) || echo ' · not on this machine' ))"
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
    if (( ! MLX_OK )); then
      echo "configure: Whisper-MLX needs an Apple Silicon Mac — pick a faster-whisper or Moonshine model" >&2
      exit 1
    fi
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
    echo "configure: -i needs an interactive terminal (stdin is not a TTY)" >&2
    exit 1
  fi

  echo "configure: interactive model picker"
  echo
  print_hardware_profile
  probe_engine_support
  echo
  print_cascades
  echo

  # --- STT ------------------------------------------------------------------
  echo "STT — speech-to-text engine + model:"
  print_slot_reccos stt
  print_stt_groups numbered
  STT_TOTAL=$STT_N
  read -r -p "choose STT [default ${DEFAULT_STT_NAME}]: " ans || ans=""
  if [[ -z "$ans" ]]; then
    resolve_stt_pick "$DEFAULT_STT_PICK"
  elif [[ "$ans" =~ ^[0-9]+$ ]] && (( ans >= 1 && ans <= STT_TOTAL )); then
    resolve_stt_pick "$ans"
  else
    echo "configure: '$ans' is not a valid choice" >&2; exit 1
  fi
  echo

  # --- LLM ------------------------------------------------------------------
  echo "LLM — Ollama model (best fits for this machine first):"
  print_slot_reccos llm
  print_catalog_table numbered
  OLD_IFS="$IFS"; IFS=$'\n'; CATALOG_ROWS=( $(sorted_catalog_rows) ); IFS="$OLD_IFS"
  read -r -p "choose LLM [default ${LOCAT_LLM_MODEL:-qwen2.5:14b}]: " ans || ans=""
  if [[ -z "$ans" ]]; then
    CHOSEN_LLM="${LOCAT_LLM_MODEL:-qwen2.5:14b}"
  elif [[ "$ans" =~ ^[0-9]+$ ]] && (( ans >= 1 && ans <= ${#CATALOG_ROWS[@]} )); then
    CHOSEN_LLM="$(echo "${CATALOG_ROWS[$((ans - 1))]}" | cut -d'|' -f3)"
  else
    CHOSEN_LLM="$ans"  # free-typed tag: allowed, judged by the suffix fallback
  fi
  LLM_GB="$(llm_needs_gb "$CHOSEN_LLM")"
  echo

  # --- TTS ------------------------------------------------------------------
  echo "TTS — text-to-speech engine + voice (all tiny next to the LLM):"
  print_slot_reccos tts
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
  echo "configure: combo check — STT ${CHOSEN_STT_ENGINE}/${CHOSEN_STT_MODEL} + LLM ${CHOSEN_LLM} + TTS ${CHOSEN_TTS_ENGINE}/${CHOSEN_VOICE}"
  TOK="$(est_tok_s "$(llm_active_gb "$CHOSEN_LLM")")"
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
    whisper_mlx)    STT_MODEL_VAR="LOCAT_WHISPER_MODEL" ;;
    faster_whisper) STT_MODEL_VAR="LOCAT_FASTER_WHISPER_MODEL" ;;
    moonshine)      STT_MODEL_VAR="LOCAT_MOONSHINE_MODEL" ;;
  esac
  case "$CHOSEN_TTS_ENGINE" in
    kokoro) TTS_VOICE_VAR="LOCAT_KOKORO_VOICE" ;;
    piper)  TTS_VOICE_VAR="LOCAT_PIPER_VOICE" ;;
  esac
  echo
  echo "  .env lines for this combo:"
  echo "     LOCAT_STT_ENGINE=${CHOSEN_STT_ENGINE}"
  echo "     ${STT_MODEL_VAR}=${CHOSEN_STT_MODEL}"
  echo "     LOCAT_LLM_MODEL=${CHOSEN_LLM}"
  echo "     LOCAT_TTS_ENGINE=${CHOSEN_TTS_ENGINE}"
  echo "     ${TTS_VOICE_VAR}=${CHOSEN_VOICE}"
  echo
  if (( ! APPROVED )); then
    read -r -p "combo was rejected — continue anyway? [y/N] " ans || ans=""
    [[ "$ans" =~ ^[Yy] ]] || { echo "configure: no changes made"; exit 1; }
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
    env_set LOCAT_STT_ENGINE "$CHOSEN_STT_ENGINE"
    env_set "$STT_MODEL_VAR" "$CHOSEN_STT_MODEL"
    env_set LOCAT_LLM_MODEL "$CHOSEN_LLM"
    env_set LOCAT_TTS_ENGINE "$CHOSEN_TTS_ENGINE"
    env_set "$TTS_VOICE_VAR" "$CHOSEN_VOICE"
    pass "wrote .env (LOCAT_STT_ENGINE, ${STT_MODEL_VAR}, LOCAT_LLM_MODEL, LOCAT_TTS_ENGINE, ${TTS_VOICE_VAR})"
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
    # Not an engine, but same rule: re-list it or this sync uninstalls pyaudio
    # and bot.py stops working.
    (( LOCAL_AUDIO_OK ))                 && EXTRA_FLAGS="$EXTRA_FLAGS --extra local-audio"
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
  NEED_EMBED=0; embed_installed || NEED_EMBED=1
  NEED_WHISPER=0
  [[ "$CHOSEN_STT_ENGINE" == "whisper_mlx" ]] && ! hf_model_installed "$CHOSEN_STT_HFDIR" && NEED_WHISPER=1
  if (( NEED_LLM || NEED_EMBED || NEED_WHISPER )); then
    echo
    (( NEED_LLM ))     && echo "  missing: LLM ${CHOSEN_LLM}"
    (( NEED_EMBED ))   && echo "  missing: embed model ${EMBED_MODEL} (RAG — ./locat.sh index-rag needs it)"
    (( NEED_WHISPER )) && echo "  missing: Whisper-MLX ${CHOSEN_STT_MODEL}"
    read -r -p "pull missing models now? (needs network) [y/N] " ans || ans=""
    if [[ "$ans" =~ ^[Yy] ]]; then
      if (( NEED_LLM || NEED_EMBED )); then
        if ollama list >/dev/null 2>&1; then
          (( NEED_LLM ))   && ! ollama pull "$CHOSEN_LLM" \
            && warn "ollama pull failed — check the tag name and network"
          (( NEED_EMBED )) && ! ollama pull "$EMBED_MODEL" \
            && warn "ollama pull ${EMBED_MODEL} failed — check the tag name and network"
        else
          warn "no Ollama server running — start ./scripts/run_ollama.sh (it pulls LOCAT_LLM_MODEL from .env on startup)"
        fi
      fi
      if (( NEED_WHISPER )); then
        if command -v uv >/dev/null 2>&1; then
          if ! LOCAT_WHISPER_MODEL="$CHOSEN_STT_MODEL" uv run python scripts/prefetch_models.py; then
            warn "whisper prefetch failed — retry with: LOCAT_WHISPER_MODEL=${CHOSEN_STT_MODEL} uv run python scripts/prefetch_models.py"
          fi
        else
          warn "uv not on PATH — install it, then: LOCAT_WHISPER_MODEL=${CHOSEN_STT_MODEL} uv run python scripts/prefetch_models.py"
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
  echo "configure: ✅ combo ready — ./locat.sh start"
  exit 0
fi

# =============================================================================
# Default / verbose modes (read-only).
# =============================================================================
print_all_catalogs() {
  echo "~ LLM catalog ~"
  echo
  echo "  Ollama (local server · ${CATALOG_STATUS:-built-in list}$( (( SHOW_ALL )) && echo " · all" || echo " · top ${CATALOG_LIMIT} that fit; ./locat.sh status -v for everything"))"
  print_catalog_table
  echo "  (pick interactively with ./configure.sh -i)"
  echo
  echo
  echo "~ STT catalog ~"
  echo
  print_stt_groups plain
  echo
  echo "~ TTS catalog ~"
  echo
  print_tts_groups plain
}

if (( HARDWARE_ONLY )); then
  print_hardware_profile
  exit 0
fi
if (( CATALOGS_ONLY )); then
  probe_engine_support
  print_all_catalogs
  exit 0
fi

echo "configure: hardware check"
if (( MLX_OK )); then
  pass "Apple Silicon Mac (${CHIP})"
  pass "${RAM_GB} GB unified memory"
else
  warn "${PLATFORM} (${CHIP}) — no Apple-GPU STT here; CPU engines apply (faster-whisper/Moonshine)"
  pass "${RAM_GB} GB memory"
fi

# --- RAM vs the configured LLM (only speak up if something's off) ------------
LOCAT_LLM_MODEL="${LOCAT_LLM_MODEL:-qwen2.5:14b}"

# Best qwen2.5 tag for this much unified memory, leaving headroom for Whisper
# (~1.6 GB), Kokoro (~0.3 GB), and the OS.
if   (( RAM_GB >= 24 )); then RECOMMEND="qwen2.5:14b"
elif (( RAM_GB >= 12 )); then RECOMMEND="qwen2.5:7b"
else                          RECOMMEND="qwen2.5:3b"
fi

NEED="$(llm_needs_gb "$LOCAT_LLM_MODEL")"
if (( NEED == 0 )); then
  warn "configured LLM '${LOCAT_LLM_MODEL}': size unknown — can't judge fit"
elif (( NEED + 4 > RAM_GB )); then
  warn "configured LLM '${LOCAT_LLM_MODEL}' wants ~${NEED} GB + overhead — tight on ${RAM_GB} GB; consider ${RECOMMEND}"
fi

echo
print_cascades

echo
echo "configure: current model configuration"
# `uv run` implicitly syncs, so this is where a broken dependency tree first
# surfaces. Show the real error instead of swallowing it — "run 'uv sync'" is
# useless advice when uv sync is itself what's failing (e.g. a package with no
# wheel for this arch falling back to a source build).
MODELS_ERR="$(mktemp)"
if ! uv run python scripts/print_models.py --bare 2>"$MODELS_ERR"; then
  warn "could not resolve models — 'uv run' failed:"
  tail -n 15 "$MODELS_ERR" | sed 's/^/         /'
  echo "         if a package is building from source, check for an arch wheel gap:"
  echo "           python3 scripts/check_wheels.py"
fi
rm -f "$MODELS_ERR"

# --- Verbose: full capability matrix ----------------------------------------
if (( VERBOSE )); then
  probe_engine_support
  echo
  echo "configure: hardware profile"
  print_hardware_profile
  if [[ -d "$LOCAT_MODEL_DIR" ]]; then
    echo "  models (LOCAT_MODEL_DIR=$LOCAT_MODEL_DIR):"
    du -sh "$LOCAT_MODEL_DIR"/*/ 2>/dev/null \
      | awk '{n=split($2,p,"/"); printf "     %-8s %s\n", $1, p[n-1]"/"}' || :
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
  echo "configure: model catalogs"
  echo
  print_all_catalogs
fi
