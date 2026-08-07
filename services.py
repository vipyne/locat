"""services.py — engine-dispatching builders for the bot's STT / LLM / TTS.

The three service builders live here, OUTSIDE the bot files, so that switching
engines (via ``.env`` / ``./doctor.sh -i``) never touches ``bot.py`` /
``bot_moq.py`` / ``bot_web.py`` — customize your pipeline freely in bot*.py.

Each builder reads its engine choice from config (``STT_ENGINE`` /
``TTS_ENGINE``) and constructs the matching Pipecat service:

    STT: whisper_mlx (default) | faster_whisper | moonshine
    LLM: Ollama (the only local LLM path)
    TTS: kokoro (default) | piper

Engine imports happen lazily inside each branch, so an engine's optional
dependency (``uv sync --extra moonshine`` / ``--extra piper``) is only required
if that engine is actually selected. Importing this module has no side effects
beyond importing config (which pins the repo-local model-cache env vars).
"""

import config

import sys
from pathlib import Path

from spoken_text_filter import SpokenTextFilter


def _engine_exit(engine: str, extra: str, exc: Exception) -> "None":
    """Exit with an actionable message when an engine's dependency is missing."""
    sys.exit(
        f"\n✖ {engine} support is not installed ({exc})\n"
        f"  Install it:  uv sync --extra {extra}\n"
        f"  (or pick a different engine with ./doctor.sh -i)\n"
    )


def build_stt():
    """Build the speech-to-text service selected by ``STT_ENGINE``."""
    engine = config.stt_engine()

    if engine == "whisper_mlx":
        from pipecat.services.whisper.stt import MLXModel, WhisperSTTServiceMLX

        model = MLXModel[config.whisper_model()]
        return WhisperSTTServiceMLX(settings=WhisperSTTServiceMLX.Settings(model=model))

    if engine == "faster_whisper":
        from pipecat.services.whisper.stt import Model, WhisperSTTService

        model = Model[config.faster_whisper_model()]
        return WhisperSTTService(settings=WhisperSTTService.Settings(model=model.value))

    if engine == "moonshine":
        try:
            from pipecat.services.moonshine.stt import Model, MoonshineSTTService
        except ImportError as exc:
            _engine_exit("Moonshine", "moonshine", exc)
        model = Model[config.moonshine_model()]
        return MoonshineSTTService(settings=MoonshineSTTService.Settings(model=model.value))

    sys.exit(
        f"\n✖ Unknown STT_ENGINE '{engine}'"
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
    """Build the text-to-speech service selected by ``TTS_ENGINE``."""
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

    sys.exit(f"\n✖ Unknown TTS_ENGINE '{engine}' — valid: kokoro (default), piper\n")
