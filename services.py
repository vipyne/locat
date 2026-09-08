"""services.py — engine-dispatching builders for the bot's STT / LLM / TTS.

The three service builders live here, OUTSIDE the bot files, so that switching
engines (via ``.env`` / ``./configure.sh -i``) never touches ``bot.py`` /
``bot_moq.py`` / ``bot_web.py`` — customize your pipeline freely in bot*.py.

Each builder reads its engine choice from config (``LOCAT_STT_ENGINE`` /
``LOCAT_TTS_ENGINE``) and constructs the matching Pipecat service:

    STT: whisper_mlx (default) | faster_whisper | moonshine
    LLM: Ollama (the only local LLM path)
    TTS: kokoro (default) | piper

Engine imports happen lazily inside each branch, so an engine's optional
dependency (``uv sync --extra moonshine`` / ``--extra piper``) is only required
if that engine is actually selected. Importing this module has no side effects
beyond importing config (which pins the repo-local model-cache env vars).
"""

import config

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

    ("store", snapshot_dir) when the locat store ($HF_HOME) holds them,
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
    """The model value handed to the STT service: the configured name when the
    locat store holds the weights (the service resolves it through $HF_HOME),
    or the external cache's snapshot path so the model loads from where it
    already is — no download, no symlinks. Both whisper backends accept a local
    directory (mlx_whisper path_or_hf_repo / faster_whisper model_size_or_path).
    """
    repo_id = stt_repo_id()
    if repo_id is None:
        return configured
    location = stt_weights_location(repo_id)
    if location is None or location[0] == "store":
        return configured
    snapshot = location[1]
    logger.info(f"STT '{repo_id}' loading weights from external cache: {snapshot}")
    return str(snapshot)


def build_stt():
    """Build the speech-to-text service selected by ``LOCAT_STT_ENGINE``."""
    engine = config.stt_engine()

    if engine == "whisper_mlx":
        if not config.IS_APPLE_SILICON:
            sys.exit(
                "\n✖ LOCAT_STT_ENGINE=whisper_mlx requires an Apple Silicon Mac (MLX only runs there)\n"
                "  Set LOCAT_STT_ENGINE=faster_whisper in .env (or run ./configure.sh -i)\n"
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
        f"\n✖ Unknown LOCAT_STT_ENGINE '{engine}'"
        f" — valid: whisper_mlx (default), faster_whisper, moonshine\n"
    )


def build_llm():
    """Build the local Ollama large-language-model service."""
    from pipecat.services.ollama.llm import OLLamaLLMService

    return OLLamaLLMService(
        settings=OLLamaLLMService.Settings(model=config.llm_model()),
        base_url=config.ollama_base_url(),
    )


def build_tts():
    """Build the text-to-speech service selected by ``LOCAT_TTS_ENGINE``."""
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

    sys.exit(f"\n✖ Unknown LOCAT_TTS_ENGINE '{engine}' — valid: kokoro (default), piper\n")
