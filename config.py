"""config.py — env-driven settings for the fully-offline voice bot.

Single source of truth for every configurable knob. All values come from the
process environment (optionally populated from ``./.env``), with sensible
defaults so ``uv run bot.py`` works with **zero configuration**.

Two kinds of settings live here:

1. **Cache-dir vars** (``HF_HOME``, Kokoro model/voices paths). These must be
   set *before* any model library is imported — Hugging Face freezes its cache
   root at ``huggingface_hub`` import time — so they are established here at
   *import time* via ``os.environ.setdefault(...)``. Importing this module
   therefore steers HF / Kokoro caches into the repo-local ``./models/`` tree
   (matching ``scripts/prefetch_models.py``, so the bot reads weights from
   exactly where the prefetch wrote them — the key to offline runs). It does
   NOT load any model, touch audio hardware, or hit the network.

   Because of the freeze-at-import behaviour, ``bot.py`` imports this module
   *before* it imports any ``pipecat`` service.

2. **Runtime settings** (model names, voice, device indices, greeting, log
   level). Exposed as getter functions read at *call time*, so they pick up
   whatever ``load_dotenv()`` in ``main()`` applied before the builders run.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
MODELS_DIR = REPO_ROOT / "models"
KOKORO_DIR = MODELS_DIR / "kokoro"

# --- Repo-local cache defaults, established at import time ------------------
# Load ./.env first so user overrides win over these defaults, then setdefault
# the cache-dir vars. Mirrors scripts/prefetch_models.py.
try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except Exception:  # python-dotenv is a dep, but never hard-fail on config import
    pass

# Hugging Face cache root (Whisper-MLX weights land here). Steered into the repo
# so a warmed-up ./models/huggingface is found offline instead of ~/.cache.
os.environ.setdefault("HF_HOME", str(MODELS_DIR / "huggingface"))
# Kokoro ONNX + voices bundle (also honored directly by build_tts via these vars).
os.environ.setdefault("KOKORO_MODEL_PATH", str(KOKORO_DIR / "kokoro-v1.0.onnx"))
os.environ.setdefault("KOKORO_VOICES_PATH", str(KOKORO_DIR / "voices-v1.0.bin"))


# --- Defaults for the runtime settings -------------------------------------
DEFAULT_WHISPER_MODEL = "LARGE_V3_TURBO"
DEFAULT_LLM_MODEL = "qwen2.5:14b"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"
DEFAULT_KOKORO_VOICE = "af_heart"
DEFAULT_GREETING = (
    "Hi. I'm your private, offline financial thinking partner. "
    "What's on your mind today?"
)
DEFAULT_GREETING_DELAY_SECS = 1.0
DEFAULT_LOG_LEVEL = "DEBUG"


def _get(name: str, default: str) -> str:
    """Return env var ``name`` (stripped), or ``default`` when unset/blank."""
    return os.getenv(name, "").strip() or default


def whisper_model() -> str:
    """``MLXModel`` member name for Whisper-MLX STT (default LARGE_V3_TURBO).

    Other members: TINY, MEDIUM, LARGE_V3. Must match a member the prefetch
    downloaded (``scripts/prefetch_models.py`` reads the same var).
    """
    return _get("WHISPER_MODEL", DEFAULT_WHISPER_MODEL)


def llm_model() -> str:
    """Ollama model tag for the LLM (default ``qwen2.5:14b``).

    The same string ``scripts/run_ollama.sh`` uses to ``ollama pull``, so the
    bot and the pull agree on which model is served.
    """
    return _get("LLM_MODEL", DEFAULT_LLM_MODEL)


def ollama_base_url() -> str:
    """OpenAI-compatible Ollama endpoint (default ``http://localhost:11434/v1``).

    Note the trailing ``/v1``: the OpenAI-compat path, not the native API root.
    """
    return _get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL)


def kokoro_voice() -> str:
    """Kokoro voice id (default ``af_heart``).

    Kokoro's shipped ``Settings.voice`` default is ``None`` (unsynthesizable),
    so the bot always supplies an explicit id.
    """
    return _get("KOKORO_VOICE", DEFAULT_KOKORO_VOICE)


def kokoro_model_path() -> str:
    """Path to the Kokoro ONNX model (default ./models/kokoro/kokoro-v1.0.onnx)."""
    return _get("KOKORO_MODEL_PATH", str(KOKORO_DIR / "kokoro-v1.0.onnx"))


def kokoro_voices_path() -> str:
    """Path to the Kokoro voices bundle (default ./models/kokoro/voices-v1.0.bin)."""
    return _get("KOKORO_VOICES_PATH", str(KOKORO_DIR / "voices-v1.0.bin"))


def _device_index(name: str) -> int | None:
    """PyAudio device index from env ``name``; None (system default) if unset/blank."""
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else None


def input_device_index() -> int | None:
    """Mic device index (INPUT_DEVICE_INDEX); None = system default input."""
    return _device_index("INPUT_DEVICE_INDEX")


def output_device_index() -> int | None:
    """Speaker device index (OUTPUT_DEVICE_INDEX); None = system default output."""
    return _device_index("OUTPUT_DEVICE_INDEX")


def greeting() -> str:
    """Opening line the bot speaks on startup (GREETING)."""
    return _get("GREETING", DEFAULT_GREETING)


def greeting_delay_secs() -> float:
    """Seconds to wait before speaking the greeting (GREETING_DELAY_SECS).

    Gives the local audio-output stream time to spin up before the first frame.
    """
    raw = os.getenv("GREETING_DELAY_SECS", "").strip()
    return float(raw) if raw else DEFAULT_GREETING_DELAY_SECS


def log_level() -> str:
    """Loguru level for stderr logging (LOG_LEVEL, default DEBUG).

    DEBUG surfaces each service's activity — useful during the offline
    verification to confirm no service silently reaches the network.
    """
    return _get("LOG_LEVEL", DEFAULT_LOG_LEVEL)
