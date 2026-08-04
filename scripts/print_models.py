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
    print(f"")
    print(f"models:  STT  {whisper_name}{whisper_repo}")
    print(f"         LLM  {config.llm_model()} (Ollama @ {config.ollama_base_url()})")
    print(f"         TTS  Kokoro {Path(config.kokoro_model_path()).name} · voice {config.kokoro_voice()}")
    print(f"")


if __name__ == "__main__":
    main()
