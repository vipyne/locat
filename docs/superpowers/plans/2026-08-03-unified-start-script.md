# Unified start.sh + doctor.sh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One `start.sh` that launches the bot on a chosen transport (`moq` default, `webrtc`, `headphones`) and prints the exact STT/LLM/TTS models, plus a standalone `doctor.sh` hardware-capability report.

**Architecture:** `start.sh` keeps the Ollama bring-up and Ctrl-C teardown shared by the three current scripts and switches only the launch command + banner per transport. A tiny `scripts/print_models.py` resolves model identities through `config.py` (single source of truth) and is shared by both shell scripts. `doctor.sh` is read-only: platform/RAM checks with advice, `-v` for the full matrix.

**Tech Stack:** bash, Python 3.12 (uv-managed venv), pipecat 1.5.0 (`MLXModel` enum in `pipecat.services.whisper.stt`), macOS `sysctl`/`system_profiler`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-03-unified-start-script-design.md`
- Transport values: exactly `moq` (default), `webrtc`, `headphones`; unknown → stderr error listing valid names, exit 1.
- `start_moq.sh` and `start_web.sh` are deleted (no wrapper shims).
- Model info must resolve through `config.py` — never duplicate defaults in bash.
- All commands via `uv` (`uv run …`), never bare `python`/pip.
- Preserve existing behavior verbatim: `.env` sourcing, Ollama probe/bring-up/900 s wait, `set -m` process-group Ctrl-C teardown (INT → wait ≤2 s → KILL).
- `doctor.sh` never launches the bot; `start.sh` never calls `doctor.sh`.
- Repo has no test framework; scripts are verified with `bash -n`, targeted runs, and observed output. Every verify step states the exact expected output.

---

### Task 1: scripts/print_models.py

**Files:**
- Create: `scripts/print_models.py`

**Interfaces:**
- Consumes: `config.whisper_model() / llm_model() / ollama_base_url() / kokoro_voice() / kokoro_model_path()`; `pipecat.services.whisper.stt.MLXModel` (StrEnum, `.value` = HF repo id).
- Produces: a 3-line stdout block starting with `models:` — consumed verbatim by `start.sh` (Task 2) and `doctor.sh` (Task 3) via `uv run python scripts/print_models.py`.

- [ ] **Step 1: Write the file**

```python
#!/usr/bin/env python3
"""Print the exact STT/LLM/TTS models the bot will load.

Shared by start.sh and doctor.sh so the printed values can never drift from
what the bot actually loads: everything resolves through config.py (which
loads ./.env), and the Whisper enum name resolves to its Hugging Face repo id
via pipecat's MLXModel enum.
"""

import sys
from pathlib import Path

# Runnable from any CWD: make the repo root importable, then import config
# FIRST (it pins the repo-local model-cache env vars at import time).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402


def main() -> None:
    whisper_name = config.whisper_model()
    try:
        from pipecat.services.whisper.stt import MLXModel

        whisper_repo = f" ({MLXModel[whisper_name].value})"
    except (ImportError, KeyError):
        # pipecat missing (deps not synced) or WHISPER_MODEL not a valid
        # enum member — still print the configured name rather than dying.
        whisper_repo = ""
    print(f"models:  STT  {whisper_name}{whisper_repo}")
    print(f"         LLM  {config.llm_model()} (Ollama @ {config.ollama_base_url()})")
    print(f"         TTS  Kokoro {Path(config.kokoro_model_path()).name} · voice {config.kokoro_voice()}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it and verify output**

Run: `uv run python scripts/print_models.py`
Expected (with default config):

```
models:  STT  LARGE_V3_TURBO (mlx-community/whisper-large-v3-turbo)
         LLM  qwen2.5:14b (Ollama @ http://localhost:11434/v1)
         TTS  Kokoro kokoro-v1.0.onnx · voice af_heart
```

- [ ] **Step 3: Verify env override is honored**

Run: `LLM_MODEL=llama3 WHISPER_MODEL=TINY uv run python scripts/print_models.py`
Expected: `STT  TINY (mlx-community/whisper-tiny)` and `LLM  llama3 (…)` lines.

- [ ] **Step 4: Commit**

```bash
git add scripts/print_models.py
git commit -m "feat: add print_models.py — resolved STT/LLM/TTS identities"
```

---

### Task 2: unified start.sh (and delete the two old scripts)

**Files:**
- Modify: `start.sh` (full rewrite, stays executable)
- Delete: `start_moq.sh`, `start_web.sh`

**Interfaces:**
- Consumes: `scripts/print_models.py` (Task 1); `scripts/run_ollama.sh`; `bot_moq.py`/`bot_web.py` (both take `--host`/`--port`); `bot.py` (no args).
- Produces: `./start.sh [-t moq|webrtc|headphones] [-h]` — referenced by README (Task 4).

- [ ] **Step 1: Rewrite start.sh with this exact content**

```bash
#!/usr/bin/env bash
#
# start.sh — one command to run the fully-offline voice bot on your transport
# of choice. Ensures the repo-local Ollama server is up (models in
# ./models/ollama) with the LLM pulled, prints the exact models about to be
# used, then launches the bot. Ollama is left running in the background so
# subsequent starts are instant; stop it with ./stop.sh.
#
# Usage:
#   ./start.sh                     # MoQ transport (default) — browser, lowest latency
#   ./start.sh -t webrtc           # SmallWebRTC transport — browser
#   ./start.sh -t headphones       # local audio hardware (use headphones! 🎧)
#   ./start.sh -h                  # show this help
#   LLM_MODEL=llama3 ./start.sh    # use a different local model
#
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"
[[ -f .env ]] && { set -a; source .env; set +a; }

usage() { grep '^#   ' "$0" | sed 's/^#   //'; }

TRANSPORT="moq"
while [[ $# -gt 0 ]]; do
  case "$1" in
    -t|--transport) TRANSPORT="${2:-}"; shift 2 ;;
    -h|--help)      usage; exit 0 ;;
    *) echo "start: unknown option '$1' (try ./start.sh -h)" >&2; exit 1 ;;
  esac
done

case "$TRANSPORT" in
  moq|webrtc|headphones) ;;
  *) echo "start: unknown transport '$TRANSPORT' — valid: moq (default), webrtc, headphones" >&2; exit 1 ;;
esac

OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
BASE="http://${OLLAMA_HOST}"
LLM_MODEL="${LLM_MODEL:-qwen2.5:14b}"
OLLAMA_LOG="$REPO/ollama.log"
WEB_PORT="${WEB_PORT:-7860}"

server_up()   { curl -sf --max-time 2 "$BASE/api/tags" -o /dev/null 2>/dev/null; }
model_ready() { curl -sf --max-time 3 "$BASE/api/tags" 2>/dev/null | grep -q "\"${LLM_MODEL}"; }

if server_up && model_ready; then
  echo "start: Ollama already serving ${LLM_MODEL} at ${BASE}"
else
  echo "start: bringing up repo-local Ollama (store → ./models/ollama; log → ollama.log)"
  # run_ollama.sh starts 'ollama serve' (if needed) + pulls the model, then blocks.
  nohup ./scripts/run_ollama.sh >"$OLLAMA_LOG" 2>&1 &
  echo "start: waiting for ${LLM_MODEL} (first pull can take a few minutes)..."
  for _ in $(seq 1 900); do model_ready && break; sleep 1; done
  model_ready || { echo "start: ${LLM_MODEL} not ready — see $OLLAMA_LOG" >&2; exit 1; }
fi

uv run python scripts/print_models.py \
  || echo "start: (could not resolve models — run 'uv sync' and retry)"

case "$TRANSPORT" in
  moq)
    CMD=(uv run python bot_moq.py --host localhost --port "${WEB_PORT}")
    echo "start: Ollama ready. Serving the MoQ bot on http://localhost:${WEB_PORT}"
    echo "       ▶ Open  http://localhost:${WEB_PORT}  — choose 'Media over QUIC' in the"
    echo "         top-left dropdown, allow the mic, and Connect."
    ;;
  webrtc)
    CMD=(uv run python bot_web.py --host localhost --port "${WEB_PORT}")
    echo "start: Ollama ready. Serving the WebRTC bot on http://localhost:${WEB_PORT}"
    echo "       ▶ Open  http://localhost:${WEB_PORT}/client  in a browser, allow the mic, and talk."
    ;;
  headphones)
    CMD=(uv run bot.py)
    echo "start: Ollama ready. Launching the local-audio bot — use headphones 🎧"
    ;;
esac
echo "       Ctrl-C stops the bot; Ollama keeps running — run ./stop.sh to shut it down."

# Run the bot in its OWN process group (set -m) so Ctrl-C is handled here in the
# launcher, not swallowed by the bot. We then tear down the whole bot tree
# (uv + python + audio threads): a plain `exec`/foreground bot can hang on audio
# teardown and leave Ctrl-C looking dead.
set -m
env LLM_MODEL="${LLM_MODEL}" "${CMD[@]}" &
BOT_PID=$!

stop_bot() {
  trap - INT TERM
  set +e
  echo
  echo "start: stopping bot…"
  kill -INT -"$BOT_PID" 2>/dev/null || kill -INT "$BOT_PID" 2>/dev/null   # try graceful
  for _ in $(seq 1 8); do kill -0 "$BOT_PID" 2>/dev/null || break; sleep 0.25; done
  kill -KILL -"$BOT_PID" 2>/dev/null || kill -KILL "$BOT_PID" 2>/dev/null  # then force
  exit 0
}
trap stop_bot INT TERM
wait "$BOT_PID"
```

- [ ] **Step 2: Syntax-check and verify flag handling**

Run: `bash -n start.sh && ./start.sh -h && ./start.sh -t bogus; echo "exit=$?"`
Expected: `-h` prints the 5 usage lines and exits 0; `-t bogus` prints
`start: unknown transport 'bogus' — valid: moq (default), webrtc, headphones` to stderr with `exit=1`.

- [ ] **Step 3: Smoke-test the default (moq) transport**

Ollama is already serving on this machine, so this is quick:

```bash
./start.sh > /private/tmp/claude-501/-Users-vipyned2-Documents-repos-vipyne-locat/d1972521-216e-4ca4-a9fb-df5c981bb998/scratchpad/start_moq_smoke.log 2>&1 &
SMOKE=$!; sleep 20
curl -skf https://localhost:7860 -o /dev/null && echo MOQ_UP || curl -sf http://localhost:7860 -o /dev/null && echo MOQ_UP
kill -INT $SMOKE; sleep 3; cat /private/tmp/claude-501/-Users-vipyned2-Documents-repos-vipyne-locat/d1972521-216e-4ca4-a9fb-df5c981bb998/scratchpad/start_moq_smoke.log
```

Expected: log shows the `models:` block, the MoQ banner, and `MOQ_UP` printed. If the port probe fails, read the log — the bot may need longer than 20 s on first model load; retry the curl before treating it as a failure. Verify no stray `bot_moq.py` process remains (`pgrep -f bot_moq.py` → empty).

- [ ] **Step 4: Smoke-test webrtc the same way**

Same as Step 3 with `./start.sh -t webrtc`, probing `http://localhost:7860/client`, expecting the WebRTC banner and no stray `bot_web.py` process after.

- [ ] **Step 5: Verify headphones transport reaches launch**

Do NOT hold a conversation — just confirm the local-audio bot starts and dies cleanly:

```bash
./start.sh -t headphones > /private/tmp/claude-501/-Users-vipyned2-Documents-repos-vipyne-locat/d1972521-216e-4ca4-a9fb-df5c981bb998/scratchpad/start_hp_smoke.log 2>&1 &
SMOKE=$!; sleep 15; kill -INT $SMOKE; sleep 3
grep -E "models:|local-audio" /private/tmp/claude-501/-Users-vipyned2-Documents-repos-vipyne-locat/d1972521-216e-4ca4-a9fb-df5c981bb998/scratchpad/start_hp_smoke.log
```

Expected: both the `models:` line and the `local-audio bot` banner appear; `pgrep -f "uv run bot.py"` → empty afterwards.

- [ ] **Step 6: Delete the old scripts and commit**

```bash
git rm start_moq.sh start_web.sh
git add start.sh
git commit -m "feat: unify start scripts — start.sh -t {moq,webrtc,headphones}"
```

---

### Task 3: doctor.sh

**Files:**
- Create: `doctor.sh` (executable: `chmod +x doctor.sh`)

**Interfaces:**
- Consumes: `scripts/print_models.py` (Task 1); `sysctl hw.memsize`, `system_profiler SPDisplaysDataType`.
- Produces: `./doctor.sh [-v|--verbose] [-h]`, exit 0 pass / 1 fail — referenced by README (Task 4).

- [ ] **Step 1: Write doctor.sh with this exact content**

```bash
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
    | awk -F': ' '/Total Number of Cores/{print $2; exit}')"
  echo "  GPU cores:  ${GPU_CORES:-unknown}"
  echo "  free disk:  $(df -h . | awk 'NR==2{print $4}') available on this volume"
  if [[ -d models ]]; then
    echo "  ./models:"
    du -sh models/*/ 2>/dev/null | awk '{printf "     %-8s %s\n", $1, $2}'
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
```

- [ ] **Step 2: Syntax-check and run default mode**

Run: `bash -n doctor.sh && chmod +x doctor.sh && ./doctor.sh; echo "exit=$?"`
Expected on this M4 Pro: ✅ Apple Silicon line with the chip name, ✅ RAM line
recommending `qwen2.5:14b` (machine has ≥24 GB), ✅ configured-LLM-fits line,
the `models:` block, final `doctor: ✅ good to go — ./start.sh`, `exit=0`.

- [ ] **Step 3: Run verbose mode**

Run: `./doctor.sh -v`
Expected: everything from Step 2 plus GPU core count (a number), free disk,
`./models` sizes, three ✅ tooling lines, the qwen2.5 table (3b/7b/14b ✅ on
this machine; 72b ❌), and the 5-row Whisper table.

- [ ] **Step 4: Verify the fail path and unknown-model path**

The Apple-Silicon FAIL branch can't run on this machine; exercise the other
branches and eyeball the fail branch code:

Run: `LLM_MODEL=qwen2.5:72b ./doctor.sh | grep '72b'` → expect the ⚠️ "wants ~47 GB … tight" line (or ❌ table row in `-v`).
Run: `LLM_MODEL=mystery:1b ./doctor.sh | grep mystery` → expect ⚠️ "size unknown".
Run: `./doctor.sh -x; echo "exit=$?"` → expect `doctor: unknown option '-x'` on stderr, `exit=1`.

- [ ] **Step 5: Commit**

```bash
git add doctor.sh
git commit -m "feat: add doctor.sh — hardware capability report (-v for full matrix)"
```

---

### Task 4: README update

**Files:**
- Modify: `README.md` (lines ~40–52 browser quickstart, ~70 headphones quickstart, ~140–144 one-command-launch paragraph, ~176–179 echo-cancellation block, ~254–256 repo tree)

**Interfaces:**
- Consumes: final CLI of `start.sh` (Task 2) and `doctor.sh` (Task 3).

- [ ] **Step 1: Update the browser quickstart (line ~50)**

Replace the `./start_web.sh` code block and its follow-up line with:

```bash
./start.sh
```

and the follow-up text: `Open http://localhost:7860, choose "Media over QUIC", click Connect & have a conversation. (Prefer WebRTC? ./start.sh -t webrtc → http://localhost:7860/client.)`

- [ ] **Step 2: Update the headphones quickstart (line ~71)**

Replace the `./start.sh` code block with:

```bash
./start.sh -t headphones
```

- [ ] **Step 3: Rewrite the one-command-launch paragraph (lines ~140–144)**

Replace with:

> **One-command launch:** `./start.sh` brings up the repo-local Ollama server (if it isn't already running), prints the exact STT/LLM/TTS models in play, and serves the MoQ browser bot — so you can skip the manual `run_ollama.sh` in step 2a. Pick a different transport with `-t`: `./start.sh -t webrtc` (browser, SmallWebRTC) or `./start.sh -t headphones` (local audio hardware) — see [echo cancellation](#how-do-you-solve-a-problem-like-echo-cancellation). Not sure what your machine can handle? `./doctor.sh` (add `-v` for the full report).

- [ ] **Step 4: Update the echo-cancellation code block (lines ~176–179)**

Replace with:

```bash
./start.sh              # MoQ/QUIC → open http://localhost:7860, pick "Media over QUIC"
./start.sh -t webrtc    # WebRTC   → open http://localhost:7860/client
```

- [ ] **Step 5: Update the repo tree (lines ~254–256)**

Replace the three `start*.sh` lines with:

```
├── start.sh                  # one command: bring up Ollama + run the bot (-t moq|webrtc|headphones)
├── doctor.sh                 # what can this machine handle? (-v for full report)
```

- [ ] **Step 6: Check for stragglers and commit**

Run: `grep -rn "start_moq\|start_web" README.md docs/ *.py *.sh` — expect matches only in `docs/superpowers/` (spec/plan history is fine).

```bash
git add README.md
git commit -m "docs: README — unified start.sh usage + doctor.sh"
```
