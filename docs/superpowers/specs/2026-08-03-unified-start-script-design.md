# Unified start script + hardware doctor — design

Date: 2026-08-03
Status: approved

## Goal

Replace the three near-identical launch scripts (`start.sh`, `start_moq.sh`,
`start_web.sh`) with a single `start.sh` that selects the transport via a flag,
prints the exact STT/LLM/TTS models before launch, and add a standalone
`doctor.sh` that reports what this machine's hardware can handle.

## start.sh

Usage:

```
./start.sh                  # MoQ transport (default) → bot_moq.py
./start.sh -t webrtc        # SmallWebRTC → bot_web.py
./start.sh -t headphones    # local audio hardware → bot.py
./start.sh -h               # usage
```

- Accepts `-t` / `--transport` with values `moq` (default), `webrtc`,
  `headphones`. Unknown value → error to stderr listing the valid names,
  exit 1.
- Keeps the logic the three scripts already share, verbatim in behavior:
  - `.env` loading (`set -a; source .env; set +a`)
  - Ollama bring-up: probe `$OLLAMA_HOST` tags endpoint, `nohup
    ./scripts/run_ollama.sh` when needed, wait up to 900s for the model
  - Process-group Ctrl-C teardown (`set -m`, `kill -INT -$BOT_PID`, escalate
    to KILL after ~2s)
- Per-transport differences only:
  - `moq` → `uv run python bot_moq.py --host localhost --port $WEB_PORT`;
    banner: open http://localhost:$WEB_PORT, pick "Media over QUIC", Connect.
  - `webrtc` → `uv run python bot_web.py --host localhost --port $WEB_PORT`;
    banner: open http://localhost:$WEB_PORT/client.
  - `headphones` → `uv run bot.py`; banner: local mic/speakers.
- Before launching, prints the exact models about to be used:

  ```
  models:  STT  LARGE_V3_TURBO (mlx-community/whisper-large-v3-turbo)
           LLM  qwen2.5:14b (Ollama @ http://localhost:11434/v1)
           TTS  Kokoro kokoro-v1.0.onnx · voice af_heart
  ```

  Resolved through `config.py` so `.env` overrides are honored and nothing
  drifts from what the bot actually loads. If resolution fails (e.g. deps not
  synced), print a soft warning and continue.
- `start_moq.sh` and `start_web.sh` are deleted.

## scripts/print_models.py

Tiny helper shared by `start.sh` and `doctor.sh`. Imports `config` (which
establishes the repo-local cache env) and `pipecat.services.whisper.stt.
MLXModel` to resolve the Whisper enum name to its exact HF repo id. Prints the
three lines above. Machine-friendly enough that doctor.sh can reuse it as-is.

## doctor.sh

Standalone hardware/capability report. Does not run the bot; `start.sh` does
not call it.

Default mode — pass/fail + advice:

- Platform check: macOS (`uname -s` == Darwin) AND Apple Silicon
  (`uname -m` == arm64). Anything else → FAIL with a clear message that
  Whisper-MLX requires Apple Silicon; exit 1.
- RAM: read `sysctl -n hw.memsize`, compare to the configured LLM, recommend
  the best-fitting qwen2.5 tag accounting for Whisper (~1.6 GB) + Kokoro
  (~0.3 GB) + OS overhead:
  - < 12 GB → `qwen2.5:3b`
  - 12–24 GB → `qwen2.5:7b`
  - ≥ 24 GB → `qwen2.5:14b`
  Flag when the configured `LLM_MODEL` is likely too big for the machine.
- Prints the resolved models (via `scripts/print_models.py`).
- Exit code: 0 pass, 1 fail.

Verbose mode (`-v` / `--verbose`) adds:

- GPU core count (`system_profiler SPDisplaysDataType` or ioreg fallback)
- Free disk on the repo volume vs sizes of `./models` subdirs
- Presence checks: `uv`, `ollama`, `curl`
- A table of qwen2.5 tags (3b/7b/14b/32b) and Whisper variants
  (TINY/MEDIUM/LARGE_V3/LARGE_V3_TURBO/LARGE_V3_TURBO_Q4) with
  can/tight/cannot verdicts for this machine's RAM.

## README

Update launch instructions to the unified `./start.sh [-t transport]` usage;
remove references to `start_moq.sh` / `start_web.sh`; add a short `doctor.sh`
mention.

## Out of scope

- Merging `bot.py` / `bot_moq.py` / `bot_web.py`
- Linux/Windows support in `doctor.sh` beyond the explicit Apple-Silicon FAIL
