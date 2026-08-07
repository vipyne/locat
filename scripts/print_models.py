#!/usr/bin/env python3
"""Print the exact STT/LLM/TTS engines + models the bot will load.

Shared by start.sh and doctor.sh so the printed values can never drift from
what the bot actually loads: everything resolves through config.py (which
loads ./.env), including the engine choice (STT_ENGINE / TTS_ENGINE) that
services.py dispatches on.
"""

import sys
from pathlib import Path

# Runnable from any CWD: make the repo root importable, then import config
# FIRST (it pins the repo-local model-cache env vars at import time).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402


def _stt_line() -> str:
    engine = config.stt_engine()
    if engine == "whisper_mlx":
        name = config.whisper_model()
        try:
            from pipecat.services.whisper.stt import MLXModel

            return f"Whisper-MLX {name} ({MLXModel[name].value})"
        except (ImportError, KeyError):
            # pipecat missing (deps not synced) or WHISPER_MODEL not a valid
            # enum member — still print the configured name rather than dying.
            return f"Whisper-MLX {name}"
    if engine == "faster_whisper":
        return f"faster-whisper {config.faster_whisper_model()} (CPU)"
    if engine == "moonshine":
        return f"Moonshine {config.moonshine_model()} (CPU)"
    return f"unknown engine '{engine}'"


def _tts_line() -> str:
    engine = config.tts_engine()
    if engine == "kokoro":
        return f"Kokoro {Path(config.kokoro_model_path()).name} · voice {config.kokoro_voice()}"
    if engine == "piper":
        return f"Piper · voice {config.piper_voice()}"
    return f"unknown engine '{engine}'"


def main() -> None:
    print("")
    print(f"models:  STT  {_stt_line()}")
    print(f"         LLM  {config.llm_model()} (Ollama @ {config.ollama_base_url()})")
    print(f"         TTS  {_tts_line()}")
    print("")


if __name__ == "__main__":
    main()
