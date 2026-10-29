"""services.py — engine-dispatching builders for the bot's STT / LLM / TTS.

The three service builders live here, OUTSIDE the bot files, so that switching
engines (via ``.env`` / ``./configure.sh -i``) never touches ``bot.py`` /
``bot_browser.py`` — customize your pipeline freely in bot*.py.

Each builder reads its engine choice from config (``LOFI_STT_ENGINE`` /
``LOFI_TTS_ENGINE``) and constructs the matching Pipecat service:

    STT: whisper_mlx (default) | faster_whisper | moonshine
    LLM: Ollama (the only local LLM path)
    TTS: kokoro (default) | piper

Online sessions take the ``build_online_*`` builders instead — Deepgram STT,
OpenAI LLM, Cartesia TTS, keyed from the three names in ``.env``.

Engine imports happen lazily inside each branch, so an engine's optional
dependency (``uv sync --extra moonshine`` / ``--extra piper``) is only required
if that engine is actually selected. Importing this module has no side effects
beyond importing config (which pins the repo-local model-cache env vars).
"""

import config

import asyncio
import os
import sys
from pathlib import Path

from loguru import logger

from scripts.consolidate import default_external_hf_hub
from scripts.print_models import hf_weights_snapshot, stt_repo_id
from spoken_text_filter import SpokenTextFilter


def _engine_exit(engine: str, extra: str, exc: Exception) -> "None":
    """Exit with an actionable message when an engine's dependency is missing."""
    sys.exit(
        f"\n✖ {engine} support is not installed ({exc})\n"
        f"  Install it:  uv sync --extra {extra}\n"
        f"  (or pick a different engine with ./configure.sh -i)\n"
    )


def stt_weights_location(repo_id: str) -> tuple[str, Path] | None:
    """Where the configured STT model's weights sit on this machine.

    ("store", snapshot_dir) when the lofi store ($HF_HOME) holds them,
    ("external", snapshot_dir) when only a fallback Hugging Face cache does,
    None when no local cache has real weight files.
    """
    store_hub = Path(os.environ["HF_HOME"]) / "hub"
    snapshot = hf_weights_snapshot(store_hub, repo_id)
    if snapshot is not None:
        return "store", snapshot
    external_hub = default_external_hf_hub(Path(config.model_dir()))
    snapshot = hf_weights_snapshot(external_hub, repo_id)
    if snapshot is not None:
        return "external", snapshot
    return None


def _stt_model_arg(configured: str) -> str:
    """The model value handed to the STT service: the local snapshot path when
    any cache holds the weights, else the configured name. A path loads
    straight from disk; a repo id makes the backend call snapshot_download,
    whose hub check stalls the first utterance ~78s with no network. Both
    whisper backends accept a local directory
    (mlx_whisper path_or_hf_repo / faster_whisper model_size_or_path).
    """
    repo_id = stt_repo_id()
    if repo_id is None:
        return configured
    location = stt_weights_location(repo_id)
    if location is None:
        return configured
    source, snapshot = location
    logger.info(f"STT '{repo_id}' loading weights from {source} cache: {snapshot}")
    return str(snapshot)


def build_stt():
    """Build the speech-to-text service selected by ``LOFI_STT_ENGINE``."""
    engine = config.stt_engine()

    if engine == "whisper_mlx":
        if not config.IS_APPLE_SILICON:
            sys.exit(
                "\n✖ LOFI_STT_ENGINE=whisper_mlx requires an Apple Silicon Mac (MLX only runs there)\n"
                "  Set LOFI_STT_ENGINE=faster_whisper in .env (or run ./configure.sh -i)\n"
            )
        from pipecat.services.whisper.stt import MLXModel, WhisperSTTServiceMLX

        model = MLXModel[config.whisper_model()]
        return WhisperSTTServiceMLX(
            settings=WhisperSTTServiceMLX.Settings(model=_stt_model_arg(model.value))
        )

    if engine == "faster_whisper":
        from pipecat.services.whisper.stt import Model, WhisperSTTService

        model = Model[config.faster_whisper_model()]
        return WhisperSTTService(
            settings=WhisperSTTService.Settings(model=_stt_model_arg(model.value))
        )

    if engine == "moonshine":
        try:
            from pipecat.services.moonshine.stt import Model, MoonshineSTTService
        except ImportError as exc:
            _engine_exit("Moonshine", "moonshine", exc)
        model = Model[config.moonshine_model()]
        return MoonshineSTTService(settings=MoonshineSTTService.Settings(model=model.value))

    sys.exit(
        f"\n✖ Unknown LOFI_STT_ENGINE '{engine}'"
        f" — valid: whisper_mlx (default), faster_whisper, moonshine\n"
    )


def online_key(name: str) -> str:
    """The named online-mode key, or exit saying which one to add."""
    key = os.getenv(name, "").strip()
    if not key:
        sys.exit(
            f"\n✖ online mode needs {name}\n"
            f"  Add it to .env (see env.example), or run an offline session\n"
        )
    return key


def build_online_stt():
    """Deepgram STT for online sessions."""
    from pipecat.services.deepgram.stt import DeepgramSTTService

    return DeepgramSTTService(
        api_key=online_key("DEEPGRAM_API_KEY"),
        settings=DeepgramSTTService.Settings(
            model=config.online_stt_model(),
            # nova-3 code-switches on "multi" — one connection covers an
            # English↔Spanish turn.
            language="multi",
        ),
    )


def build_online_llm(temperature: float | None = None):
    """OpenAI LLM for online sessions.

    ``temperature``: optional override, same contract as ``build_llm``.
    """
    from pipecat.services.openai.llm import OpenAILLMService

    settings = OpenAILLMService.Settings(model=config.online_llm_model())
    if temperature is not None:
        settings.temperature = temperature
    return OpenAILLMService(api_key=online_key("OPENAI_API_KEY"), settings=settings)


def build_online_backend_llm():
    """OpenAI LLM for the online documents analyst (LOFI_ONLINE_RAG_LLM_MODEL)."""
    from pipecat.services.openai.llm import OpenAILLMService

    return OpenAILLMService(
        api_key=online_key("OPENAI_API_KEY"),
        settings=OpenAILLMService.Settings(model=config.online_rag_llm_model()),
    )


def build_online_tts():
    """Cartesia TTS for online sessions."""
    from pipecat.services.cartesia.tts import CartesiaTTSService

    return CartesiaTTSService(
        api_key=online_key("CARTESIA_API_KEY"),
        settings=CartesiaTTSService.Settings(voice=config.online_tts_voice()),
        text_filters=[SpokenTextFilter()],
    )


def build_llm(temperature: float | None = None):
    """Build the conversation LLM (local Ollama, the offline path).

    ``temperature``: optional override — the split front runs at 0 (measured:
    qwen2.5:7b delegates 3/3 at temp 0 vs 2/3 at 0.7).
    """
    from pipecat.services.ollama.llm import OLLamaLLMService

    # Reasoning models (nemotron, qwen3) think silently for up to minutes
    # before speakable text; "none" turns thinking off. No-op for
    # non-reasoning models.
    settings = OLLamaLLMService.Settings(
        model=config.llm_model(),
        extra={"extra_body": {"reasoning_effort": "none"}},
    )
    if temperature is not None:
        settings.temperature = temperature
    return OLLamaLLMService(
        settings=settings,
        base_url=config.ollama_base_url(),
    )


def build_cascade_llm():
    """The offline cascade's LLM: holds the four records tools directly on
    LOFI_RAG_LLM_MODEL (no split front/analyst).

    Reasoning-off for the same reason as ``build_llm``: qwen3 ``<think>``
    must not reach TTS.
    """
    from pipecat.services.ollama.llm import OLLamaLLMService

    return OLLamaLLMService(
        settings=OLLamaLLMService.Settings(
            model=config.rag_llm_model(),
            extra={"extra_body": {"reasoning_effort": "none"}},
        ),
        base_url=config.ollama_base_url(),
    )


def build_backend_llm():
    """The records backend's LLM (LOFI_RAG_LLM_MODEL, thinking allowed).

    No reasoning-off override: the BackendLLMWorker renders model output as
    spoken-or-note, so thinking never reaches TTS; pipeline.py additionally
    strips any inline <think> blocks from backend output.
    """
    from pipecat.services.ollama.llm import OLLamaLLMService

    return OLLamaLLMService(
        settings=OLLamaLLMService.Settings(model=config.rag_llm_model()),
        base_url=config.ollama_base_url(),
    )


# Analyst cold load (qwen3:8b): 11.5 s TTFB on the first delegation, on top
# of thinking time — the user hears a long silence.
WARMUP_TIMEOUT_SECS = 120.0

_warmups: set[asyncio.Task] = set()


async def warm_analyst_model(system_messages=None, client=None) -> None:
    """Load the analyst's weights into Ollama — and, given its system
    messages, prefill them so the KV prefix cache is warm before the first
    delegation (25s first-delegation TTFB = prefill of the composed system
    prompt). Without messages: empty /api/generate =
    load, no tokens.

    Errors are swallowed: a warmup that fails costs the cold load back, and
    bot._preflight_llm already reports an unreachable Ollama or missing model.
    """
    import httpx

    http = client or httpx.AsyncClient(timeout=WARMUP_TIMEOUT_SECS)
    model = config.rag_llm_model()
    try:
        if system_messages:
            await http.post(
                f"{config.ollama_base_url()}/chat/completions",
                json={
                    "model": model,
                    "messages": system_messages,
                    "max_tokens": 1,
                    "stream": False,
                },
            )
            logger.info(f"Analyst warmup done: {model} loaded, system prompt prefilled")
        else:
            await http.post(
                f"{config.ollama_api_url()}/api/generate",
                json={"model": model, "prompt": "", "stream": False},
            )
            logger.info(f"Analyst warmup done: {model} loaded")
    except Exception as exc:  # noqa: BLE001 (warmup is best-effort)
        logger.debug(f"Analyst warmup skipped ({model}): {exc}")
    finally:
        if client is None:
            await http.aclose()


def warm_analyst(system_messages=None) -> None:
    """Fire the analyst warmup in the background; never blocks startup.

    No-op without a running loop (pipeline built outside asyncio, e.g. tests).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(warm_analyst_model(system_messages=system_messages))
    # Hold a reference — a bare task can be garbage-collected mid-flight.
    _warmups.add(task)
    task.add_done_callback(_warmups.discard)


def build_tts():
    """Build the text-to-speech service selected by ``LOFI_TTS_ENGINE``."""
    engine = config.tts_engine()

    if engine == "kokoro":
        from pipecat.services.kokoro.tts import KokoroTTSService

        return KokoroTTSService(
            settings=KokoroTTSService.Settings(voice=config.kokoro_voice()),
            model_path=config.kokoro_model_path(),
            voices_path=config.kokoro_voices_path(),
            text_filters=[SpokenTextFilter()],
        )

    if engine == "piper":
        try:
            from pipecat.services.piper.tts import PiperTTSService
        except ImportError as exc:
            _engine_exit("Piper", "piper", exc)
        download_dir = Path(config.piper_download_dir())
        download_dir.mkdir(parents=True, exist_ok=True)
        return PiperTTSService(
            settings=PiperTTSService.Settings(voice=config.piper_voice()),
            download_dir=download_dir,
            text_filters=[SpokenTextFilter()],
        )

    sys.exit(f"\n✖ Unknown LOFI_TTS_ENGINE '{engine}' — valid: kokoro (default), piper\n")
