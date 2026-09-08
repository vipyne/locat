"""config.py — env-driven settings for the fully-offline voice bot.

Single source of truth for every configurable knob. All values come from the
process environment (optionally populated from ``./.env``), with sensible
defaults so ``uv run bot.py`` works with **zero configuration**.

Two kinds of settings live here:

1. **Cache-dir vars** (``HF_HOME``, Kokoro/Piper paths, ``OLLAMA_MODELS``), all
   derived from ``LOCAT_MODEL_DIR`` — the single directory every downloaded
   model lives under, which can be anywhere on the machine. These must be set
   *before* any model library is imported — Hugging Face freezes its cache root
   at ``huggingface_hub`` import time — so they are established here at *import
   time* via ``os.environ.setdefault(...)``. Importing this module therefore
   steers every engine's cache into that one tree (matching
   ``scripts/prefetch_models.py`` and ``scripts/model_dir.sh``, so the bot reads
   weights from exactly where the prefetch wrote them — the key to offline
   runs). It does NOT load any model, touch audio hardware, or hit the network.

   Because of the freeze-at-import behaviour, ``bot.py`` imports this module
   *before* it imports any ``pipecat`` service.

2. **Runtime settings** (model names, voice, device indices, greeting, log
   level). Exposed as getter functions read at *call time*, so they pick up
   whatever ``load_dotenv()`` in ``main()`` applied before the builders run.
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL_DIR = "./models"

# --- Repo-local cache defaults, established at import time ------------------
# Load ./.env FIRST — before resolving LOCAT_MODEL_DIR or setdefault-ing any
# cache var — so user overrides win over these defaults. Mirrors
# scripts/model_dir.sh (bash) and scripts/prefetch_models.py.
try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except Exception:  # python-dotenv is a dep, but never hard-fail on config import
    pass


def _resolve_repo_path(raw: str) -> Path:
    """Shared resolution rules for every LOCAT_* directory var: a leading ``~``
    is expanded, and relative paths resolve against the REPO ROOT rather than
    the process's cwd, so the value means the same thing no matter where the
    bot was launched from.
    """
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    # normpath, not Path.resolve(): resolve() would follow symlinks (on macOS
    # /tmp/x comes back as /private/tmp/x), which surprises anyone who
    # deliberately points this at a symlinked disk and would also drift from
    # scripts/model_dir.sh, which cannot resolve symlinks for a path that does
    # not exist yet. Collapsing "." / ".." textually is all that is needed.
    return Path(os.path.normpath(path))


def _resolve_model_dir() -> Path:
    """Absolute path of the one directory holding every downloaded model.

    ``LOCAT_MODEL_DIR`` (default ``./models``) can point anywhere on the machine
    — an external disk, a shared cache, whatever. ``scripts/model_dir.sh``
    implements the identical resolution rules for the shell scripts that cannot
    import this module.
    """
    return _resolve_repo_path(os.getenv("LOCAT_MODEL_DIR", "").strip() or DEFAULT_MODEL_DIR)


MODELS_DIR = _resolve_model_dir()
KOKORO_DIR = MODELS_DIR / "kokoro"

# Every model store hangs off MODELS_DIR so one directory holds the lot. These
# are setdefault, not assignment: an explicitly-set HF_HOME (etc.) still wins.
#
# Hugging Face cache root — Whisper-MLX, faster-whisper AND Moonshine weights
# all land here. Steered into MODELS_DIR so a warmed-up tree is found offline
# instead of ~/.cache.
os.environ.setdefault("HF_HOME", str(MODELS_DIR / "huggingface"))
# Silence HuggingFace's download progress bars ("Fetching N files", "Reconstruction
# complete") — they clutter the bot's logs on first-use model fetches. Weights are
# still downloaded; only the noisy tqdm output is suppressed.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
# Kokoro ONNX + voices bundle (also honored directly by build_tts via these vars).
os.environ.setdefault("LOCAT_KOKORO_MODEL_PATH", str(KOKORO_DIR / "kokoro-v1.0.onnx"))
os.environ.setdefault("LOCAT_KOKORO_VOICES_PATH", str(KOKORO_DIR / "voices-v1.0.bin"))
# Piper's voice download dir. Nothing external reads this — services.py passes
# it to PiperTTSService(download_dir=...) — but it is kept in the environment so
# configure.sh (which cannot import this module) probes the same directory.
os.environ.setdefault("LOCAT_PIPER_DOWNLOAD_DIR", str(MODELS_DIR / "piper"))
# Ollama's store. Read by the `ollama` BINARY, not by any Python package: the bot
# only talks to the server over HTTP. scripts/run_ollama.sh is what actually
# exports it into `ollama serve`; setting it here keeps configure.sh and
# print_models.py reporting the same path the server uses.
os.environ.setdefault("OLLAMA_MODELS", str(MODELS_DIR / "ollama"))


# --- Legacy env names (everything we own gained a LOCAT_ prefix) ------------
# Renaming is silent by nature: an old key in .env is simply never read, and the
# bot comes up on defaults with no hint that the setting was dropped. Detect and
# say so instead.
_LEGACY_ENV_NAMES = (
    "STT_ENGINE", "TTS_ENGINE", "WHISPER_MODEL", "FASTER_WHISPER_MODEL",
    "MOONSHINE_MODEL", "KOKORO_VOICE", "KOKORO_MODEL_PATH", "KOKORO_VOICES_PATH",
    "PIPER_VOICE", "PIPER_DOWNLOAD_DIR", "LLM_MODEL", "OLLAMA_BASE_URL",
    "INPUT_DEVICE_INDEX", "OUTPUT_DEVICE_INDEX", "GREETING",
    "GREETING_DELAY_SECS", "LOG_LEVEL", "VAD_CONFIDENCE", "VAD_MIN_VOLUME",
    "VAD_START_SECS", "VAD_STOP_SECS", "WEB_PORT",
)


def legacy_env_keys() -> list[str]:
    """Pre-prefix keys still present in ``./.env``.

    Only the repo's own .env is scanned, never the ambient environment: names
    like ``LOG_LEVEL`` and ``GREETING`` are generic enough that someone's shell
    profile could export them for entirely unrelated reasons, and warning about
    that would be noise. A key in .env, by contrast, was meant for this bot.
    """
    path = REPO_ROOT / ".env"
    if not path.is_file():
        return []
    found = []
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key = line.split("=", 1)[0].strip()
            if key in _LEGACY_ENV_NAMES:
                found.append(key)
    except OSError:
        return []
    return found


_legacy = legacy_env_keys()
if _legacy:
    print(
        "locat: WARNING — these .env keys are no longer read; every setting this "
        "repo owns now takes a LOCAT_ prefix (the unprefixed names left are the "
        "four read by huggingface_hub and ollama — see env.example):\n"
        + "\n".join(f"    {k}  ->  LOCAT_{k}" for k in _legacy),
        file=sys.stderr,
    )


# --- Defaults for the runtime settings -------------------------------------
# Whisper-MLX needs an Apple-Silicon GPU (MLX ships no other macOS builds);
# everywhere else the CPU path (faster-whisper) is the working default.
IS_APPLE_SILICON = platform.system() == "Darwin" and platform.machine() == "arm64"
DEFAULT_STT_ENGINE = "whisper_mlx" if IS_APPLE_SILICON else "faster_whisper"
DEFAULT_TTS_ENGINE = "kokoro"
DEFAULT_WHISPER_MODEL = "LARGE_V3_TURBO"
DEFAULT_FASTER_WHISPER_MODEL = "DISTIL_MEDIUM_EN"
DEFAULT_MOONSHINE_MODEL = "SMALL_STREAMING"
DEFAULT_PIPER_VOICE = "en_US-lessac-medium"
DEFAULT_LLM_MODEL = "qwen2.5:14b"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"
DEFAULT_EMBED_MODEL = "nomic-embed-text"
DEFAULT_RAG_DATA_DIR = "data"
DEFAULT_RAG_TOP_K = 4
DEFAULT_RAG_CHUNK_TOKENS = 500
DEFAULT_RAG_CHUNK_OVERLAP = 50
DEFAULT_KOKORO_VOICE = "af_heart"
DEFAULT_GREETING = (
    "Hi. I'm your private, offline financial thinking partner. "
    "What's on your mind today?"
)
DEFAULT_GREETING_DELAY_SECS = 1.0
DEFAULT_MOQ_SUBSCRIBER_TIMEOUT = 15.0
DEFAULT_LOG_LEVEL = "DEBUG"

# --- Silero VAD tuning ------------------------------------------------------
# Pipecat gates speech on BOTH a neural confidence score AND an absolute EBU-R128
# loudness (`min_volume`). Silero's confidence is level-robust; the loudness gate
# is level-SENSITIVE and is what would otherwise force per-machine mic calibration.
# We default min_volume to 0.0 (gate OFF) so the bot trusts the neural score and
# "just works" across mics/machines regardless of input level. Raise it (e.g.
# 0.3-0.6) only if a loud/noisy room causes false triggers.
DEFAULT_VAD_CONFIDENCE = 0.7   # Silero speech probability required (0..1)
DEFAULT_VAD_MIN_VOLUME = 0.0   # absolute-loudness gate; 0 = disabled (portable)
DEFAULT_VAD_START_SECS = 0.2   # sustained speech before "user started"
DEFAULT_VAD_STOP_SECS = 0.2    # sustained silence before "user stopped"


def _get(name: str, default: str) -> str:
    """Return env var ``name`` (stripped), or ``default`` when unset/blank."""
    return os.getenv(name, "").strip() or default


def model_dir() -> str:
    """The one directory every downloaded model lives under (LOCAT_MODEL_DIR).

    Resolved to an absolute path at import time; see ``_resolve_model_dir``.
    Point it anywhere — ``LOCAT_MODEL_DIR=/Volumes/T7/locat-models`` moves the
    HF cache, Kokoro, Piper and Ollama stores together.
    """
    return str(MODELS_DIR)


def stt_engine() -> str:
    """Which STT engine to build (LOCAT_STT_ENGINE; default ``whisper_mlx`` on
    Apple Silicon, ``faster_whisper`` elsewhere).

    Options (see services.build_stt): ``whisper_mlx`` (Apple-GPU Whisper via MLX,
    multilingual), ``faster_whisper`` (CPU Whisper via CTranslate2 — the
    non-Apple-Silicon path), ``moonshine`` (tiny/fast CPU ONNX, English + a few
    languages; needs ``uv sync --extra moonshine``).
    """
    return _get("LOCAT_STT_ENGINE", DEFAULT_STT_ENGINE)


def tts_engine() -> str:
    """Which TTS engine to build (LOCAT_TTS_ENGINE, default ``kokoro``).

    Options (see services.build_tts): ``kokoro`` (ONNX, ~0.3 GB, 50+ voices),
    ``piper`` (in-process piper-tts, small fast voices; needs
    ``uv sync --extra piper``).
    """
    return _get("LOCAT_TTS_ENGINE", DEFAULT_TTS_ENGINE)


def whisper_model() -> str:
    """``MLXModel`` member name for Whisper-MLX STT (default LARGE_V3_TURBO).

    Other members: TINY, MEDIUM, LARGE_V3. Must match a member the prefetch
    downloaded (``scripts/prefetch_models.py`` reads the same var).
    """
    return _get("LOCAT_WHISPER_MODEL", DEFAULT_WHISPER_MODEL)


def faster_whisper_model() -> str:
    """``Model`` member name for faster-whisper STT (default DISTIL_MEDIUM_EN).

    Only used when LOCAT_STT_ENGINE=faster_whisper. Members (pipecat
    ``services.whisper.stt.Model``): TINY, BASE, SMALL, MEDIUM, LARGE,
    LARGE_V3_TURBO, DISTIL_LARGE_V2, DISTIL_MEDIUM_EN (English-only).
    Weights download from Hugging Face on first use (cached under HF_HOME).
    """
    return _get("LOCAT_FASTER_WHISPER_MODEL", DEFAULT_FASTER_WHISPER_MODEL)


def moonshine_model() -> str:
    """``Model`` member name for Moonshine STT (default SMALL_STREAMING).

    Only used when LOCAT_STT_ENGINE=moonshine. Members (pipecat
    ``services.moonshine.stt.Model``): TINY, BASE, TINY_STREAMING,
    BASE_STREAMING, SMALL_STREAMING, MEDIUM_STREAMING. Weights download from
    the Moonshine hub on first use.
    """
    return _get("LOCAT_MOONSHINE_MODEL", DEFAULT_MOONSHINE_MODEL)


def piper_voice() -> str:
    """Piper voice id (default ``en_US-lessac-medium``).

    Only used when LOCAT_TTS_ENGINE=piper. Any id from the Piper voices collection
    (huggingface.co/rhasspy/piper-voices) works; the ~60 MB voice model
    downloads on first use into ``$LOCAT_MODEL_DIR/piper/``.
    """
    return _get("LOCAT_PIPER_VOICE", DEFAULT_PIPER_VOICE)


def piper_download_dir() -> str:
    """Directory Piper voices download into (LOCAT_PIPER_DOWNLOAD_DIR).

    Defaults to ``$LOCAT_MODEL_DIR/piper`` — every checkpoint the bot needs
    stays inside the one model directory, like the other engines.
    """
    return _get("LOCAT_PIPER_DOWNLOAD_DIR", str(MODELS_DIR / "piper"))


def ollama_models_dir() -> str:
    """Directory Ollama keeps the LLM in (OLLAMA_MODELS).

    Defaults to ``$LOCAT_MODEL_DIR/ollama``. Set by scripts/run_ollama.sh before
    it starts the server; exposed here so configure.sh and print_models.py can
    report the same path the server actually uses.
    """
    return _get("OLLAMA_MODELS", str(MODELS_DIR / "ollama"))


def llm_model() -> str:
    """Ollama model tag for the LLM (default ``qwen2.5:14b``).

    The same string ``scripts/run_ollama.sh`` uses to ``ollama pull``, so the
    bot and the pull agree on which model is served.
    """
    return _get("LOCAT_LLM_MODEL", DEFAULT_LLM_MODEL)


def ollama_base_url() -> str:
    """OpenAI-compatible Ollama endpoint (default ``http://localhost:11434/v1``).

    Note the trailing ``/v1``: the OpenAI-compat path, not the native API root.
    """
    return _get("LOCAT_OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL)


def ollama_api_url() -> str:
    """Native Ollama API root (``/api/embed``, ``/api/tags``).

    Derived from ``ollama_base_url()`` by dropping the OpenAI-compat ``/v1``
    suffix — one host var covers both APIs.
    """
    return ollama_base_url().rstrip("/").removesuffix("/v1")


def embed_model() -> str:
    """Ollama model tag for RAG embeddings (LOCAT_EMBED_MODEL, default
    ``nomic-embed-text``).

    Served by the same Ollama instance as the LLM; pull it with
    ``ollama pull nomic-embed-text``.
    """
    return _get("LOCAT_EMBED_MODEL", DEFAULT_EMBED_MODEL)


def rag_data_dir() -> str:
    """Directory of the documents to index (LOCAT_RAG_DATA_DIR, default ``./data``).

    Drop ``.txt``/``.md``/``.pdf`` files here, then ``./locat.sh index-rag``.
    Resolved like ``LOCAT_MODEL_DIR``: absolute, ``~``, or relative to the repo
    root.
    """
    return str(_resolve_repo_path(_get("LOCAT_RAG_DATA_DIR", DEFAULT_RAG_DATA_DIR)))


def rag_index_dir() -> str:
    """Where the built RAG index lives (LOCAT_RAG_INDEX_DIR, default
    ``$LOCAT_MODEL_DIR/rag-index``): chunks.jsonl + embeddings.npy +
    manifest.json, all written by ``./locat.sh index-rag``.
    """
    return str(_resolve_repo_path(_get("LOCAT_RAG_INDEX_DIR", str(MODELS_DIR / "rag-index"))))


def rag_top_k() -> int:
    """Retrieved chunks injected into context per user turn (LOCAT_RAG_TOP_K, default 4)."""
    return _get_int("LOCAT_RAG_TOP_K", DEFAULT_RAG_TOP_K)


def rag_chunk_tokens() -> int:
    """Chunk budget in whitespace-split words (LOCAT_RAG_CHUNK_TOKENS, default 500)."""
    return _get_int("LOCAT_RAG_CHUNK_TOKENS", DEFAULT_RAG_CHUNK_TOKENS)


def rag_chunk_overlap() -> int:
    """Words repeated between consecutive chunks (LOCAT_RAG_CHUNK_OVERLAP, default 50)."""
    return _get_int("LOCAT_RAG_CHUNK_OVERLAP", DEFAULT_RAG_CHUNK_OVERLAP)


def kokoro_voice() -> str:
    """Kokoro voice id (default ``af_heart``).

    Kokoro's shipped ``Settings.voice`` default is ``None`` (unsynthesizable),
    so the bot always supplies an explicit id.
    """
    return _get("LOCAT_KOKORO_VOICE", DEFAULT_KOKORO_VOICE)


def kokoro_model_path() -> str:
    """Kokoro ONNX model path (default $LOCAT_MODEL_DIR/kokoro/kokoro-v1.0.onnx)."""
    return _get("LOCAT_KOKORO_MODEL_PATH", str(KOKORO_DIR / "kokoro-v1.0.onnx"))


def kokoro_voices_path() -> str:
    """Kokoro voices bundle path (default $LOCAT_MODEL_DIR/kokoro/voices-v1.0.bin)."""
    return _get("LOCAT_KOKORO_VOICES_PATH", str(KOKORO_DIR / "voices-v1.0.bin"))


def _device_index(name: str) -> int | None:
    """PyAudio device index from env ``name``; None (system default) if unset/blank."""
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else None


def input_device_index() -> int | None:
    """Mic device index (LOCAT_INPUT_DEVICE_INDEX); None = system default input."""
    return _device_index("LOCAT_INPUT_DEVICE_INDEX")


def output_device_index() -> int | None:
    """Speaker device index (LOCAT_OUTPUT_DEVICE_INDEX); None = system default output."""
    return _device_index("LOCAT_OUTPUT_DEVICE_INDEX")


def greeting() -> str:
    """Opening line the bot speaks on startup (LOCAT_GREETING)."""
    return _get("LOCAT_GREETING", DEFAULT_GREETING)


def greeting_delay_secs() -> float:
    """Seconds to wait before speaking the greeting (LOCAT_GREETING_DELAY_SECS).

    Gives the local audio-output stream time to spin up before the first frame.
    """
    raw = os.getenv("LOCAT_GREETING_DELAY_SECS", "").strip()
    return float(raw) if raw else DEFAULT_GREETING_DELAY_SECS


def moq_subscriber_timeout() -> float:
    """Max seconds the MoQ bot holds its greeting for the browser's audio
    subscription (LOCAT_MOQ_SUBSCRIBER_TIMEOUT).

    MoQ is live media with no replay — a greeting spoken before the browser
    subscribes is silently dropped. On timeout the bot greets anyway.
    """
    return _get_float("LOCAT_MOQ_SUBSCRIBER_TIMEOUT", DEFAULT_MOQ_SUBSCRIBER_TIMEOUT)


def log_level() -> str:
    """Loguru level for stderr logging (LOCAT_LOG_LEVEL, default DEBUG).

    DEBUG surfaces each service's activity — useful during the offline
    verification to confirm no service silently reaches the network.
    """
    return _get("LOCAT_LOG_LEVEL", DEFAULT_LOG_LEVEL)


def _get_float(name: str, default: float) -> float:
    """Return env var ``name`` parsed as float, or ``default`` when unset/blank."""
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else default


def _get_int(name: str, default: int) -> int:
    """Return env var ``name`` parsed as int, or ``default`` when unset/blank."""
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def vad_confidence() -> float:
    """Silero speech-probability threshold (LOCAT_VAD_CONFIDENCE, default 0.7)."""
    return _get_float("LOCAT_VAD_CONFIDENCE", DEFAULT_VAD_CONFIDENCE)


def vad_min_volume() -> float:
    """Absolute-loudness gate (LOCAT_VAD_MIN_VOLUME, default 0.0 = disabled).

    0.0 makes turn detection level-independent (portable across mics/machines).
    Raise toward 0.3-0.6 to reject low-level background noise on a loud setup.
    """
    return _get_float("LOCAT_VAD_MIN_VOLUME", DEFAULT_VAD_MIN_VOLUME)


def vad_start_secs() -> float:
    """Sustained speech before 'user started speaking' (LOCAT_VAD_START_SECS, default 0.2)."""
    return _get_float("LOCAT_VAD_START_SECS", DEFAULT_VAD_START_SECS)


def vad_stop_secs() -> float:
    """Sustained silence before 'user stopped speaking' (LOCAT_VAD_STOP_SECS, default 0.2)."""
    return _get_float("LOCAT_VAD_STOP_SECS", DEFAULT_VAD_STOP_SECS)
