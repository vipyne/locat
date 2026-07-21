#!/usr/bin/env python
"""prefetch_models.py — one-time ONLINE warm-up.

The whole point of ``locat`` is a bot that runs 100% offline. But four of its
model-backed components fetch their weights from the network on first use:

    * Whisper-MLX (STT)  — downloads from the Hugging Face hub on first transcribe.
    * Kokoro (TTS)       — downloads the ONNX model + voices bundle on construction.
    * Silero VAD         — *bundled inside the pipecat package* (no download).
    * Smart Turn v3      — *bundled inside the pipecat package* (no download).

This script forces the two that DO hit the network to download NOW, while you
still have connectivity, and it steers every cache into ``./models/`` (gitignored)
so all checkpoints live next to the code. Run it once:

    uv run python scripts/prefetch_models.py

After it finishes you can turn off Wi-Fi and ``uv run bot.py`` will find every
weight locally. (Ollama's LLM is pulled separately by ``scripts/run_ollama.sh``.)

Config knobs (env or ./.env, all optional — sensible defaults):
    HF_HOME              Hugging Face cache root      (default ./models/huggingface)
    WHISPER_MODEL        MLXModel member name         (default LARGE_V3_TURBO)
    KOKORO_MODEL_PATH    Kokoro ONNX destination      (default ./models/kokoro/kokoro-v1.0.onnx)
    KOKORO_VOICES_PATH   Kokoro voices destination    (default ./models/kokoro/voices-v1.0.bin)
"""

from __future__ import annotations

import os
from pathlib import Path

# --- Resolve repo-local cache locations and export them BEFORE importing any
#     model library, so Hugging Face / MLX / Kokoro see them at import time. ----
REPO_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = REPO_ROOT / "models"

# Load ./.env first (if present) so user overrides win, then fill defaults.
try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except Exception:  # python-dotenv should be installed, but never hard-fail here.
    pass

os.environ.setdefault("HF_HOME", str(MODELS_DIR / "huggingface"))

KOKORO_DIR = MODELS_DIR / "kokoro"
KOKORO_MODEL_PATH = Path(
    os.environ.setdefault("KOKORO_MODEL_PATH", str(KOKORO_DIR / "kokoro-v1.0.onnx"))
)
KOKORO_VOICES_PATH = Path(
    os.environ.setdefault("KOKORO_VOICES_PATH", str(KOKORO_DIR / "voices-v1.0.bin"))
)
WHISPER_MODEL = os.environ.setdefault("WHISPER_MODEL", "LARGE_V3_TURBO")

# Make sure the target directories exist before anything writes into them.
Path(os.environ["HF_HOME"]).mkdir(parents=True, exist_ok=True)
KOKORO_DIR.mkdir(parents=True, exist_ok=True)

from loguru import logger  # noqa: E402  (import after env is set, on purpose)


def prefetch_whisper_mlx() -> None:
    """Force the Whisper-MLX weights into the HF cache (HF_HOME).

    ``WhisperSTTServiceMLX`` loads lazily (its ``_load`` is a no-op) and only
    downloads on the first transcription, so instantiating it does nothing. We
    resolve the model's Hugging Face repo id from the ``MLXModel`` enum and pull
    a full snapshot up front instead.
    """
    from huggingface_hub import snapshot_download

    from pipecat.services.whisper.stt import MLXModel

    try:
        repo_id = MLXModel[WHISPER_MODEL].value
    except KeyError as exc:
        valid = ", ".join(m.name for m in MLXModel)
        raise SystemExit(
            f"WHISPER_MODEL='{WHISPER_MODEL}' is not a valid MLXModel. Choose one of: {valid}"
        ) from exc

    logger.info(f"Whisper-MLX: downloading '{repo_id}' → {os.environ['HF_HOME']} ...")
    path = snapshot_download(repo_id=repo_id)
    logger.success(f"Whisper-MLX ready: {path}")


def prefetch_kokoro() -> None:
    """Force Kokoro's ONNX model + voices bundle onto disk under ./models/kokoro.

    ``KokoroTTSService.__init__`` calls ``_ensure_model_files(model_path, voices_path)``,
    which downloads both files if they are missing, then loads them. Passing our
    repo-local paths makes the download land exactly where the bot will look.
    """
    from pipecat.services.kokoro.tts import KokoroTTSService

    logger.info(
        f"Kokoro: ensuring model → {KOKORO_MODEL_PATH} and voices → {KOKORO_VOICES_PATH} ..."
    )
    # Construction triggers the download + a load of the ONNX model.
    KokoroTTSService(
        model_path=str(KOKORO_MODEL_PATH),
        voices_path=str(KOKORO_VOICES_PATH),
    )
    logger.success("Kokoro ready.")


def verify_bundled_models() -> None:
    """Instantiate the components whose weights ship inside the pipecat package.

    Silero VAD and Smart Turn v3 do NOT download anything — their ONNX files are
    packaged and loaded via importlib.resources. We construct them anyway so the
    warm-up run also confirms they load cleanly (a fast, offline sanity check).
    """
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
    from pipecat.audio.vad.silero import SileroVADAnalyzer

    logger.info("Silero VAD: loading bundled model (no download) ...")
    SileroVADAnalyzer()
    logger.success("Silero VAD ready (bundled).")

    logger.info("Smart Turn v3: loading bundled ONNX model (no download) ...")
    LocalSmartTurnAnalyzerV3()
    logger.success("Smart Turn v3 ready (bundled).")


def main() -> None:
    logger.info(f"Prefetching models into {MODELS_DIR} (HF_HOME={os.environ['HF_HOME']})")
    prefetch_whisper_mlx()
    prefetch_kokoro()
    verify_bundled_models()
    logger.success(
        "All local models prefetched. You can now run the bot fully offline "
        "(after `scripts/run_ollama.sh` has pulled the LLM)."
    )


if __name__ == "__main__":
    main()
