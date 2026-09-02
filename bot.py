"""Fully-offline Pipecat voice bot — headphones / system-audio entry point.

Run with: `uv run bot.py`

The full-duplex pipeline itself is assembled by pipeline.py's build_pipeline(),
shared with the browser bots; this module owns only what is transport-specific.

Everything runs locally: LocalAudioTransport (mic + speakers), Silero VAD + Local
Smart Turn v3 for turn-taking, and the STT/LLM/TTS engines built by services.py
(defaults: Whisper-MLX, Ollama, Kokoro — swappable via LOCAT_STT_ENGINE / LOCAT_TTS_ENGINE in
.env or ./doctor.sh -i). No cloud services, no API keys.

Importing this module has no side effects (no audio device access, no model loads) —
all hardware/model construction happens inside the builder functions and `main()`.
"""

import asyncio
import json
import sys
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

# Import config FIRST — before any pipecat/HF import. config sets HF_HOME (and the
# Kokoro cache paths) at import time, and Hugging Face freezes its cache root when
# huggingface_hub is imported, so this ordering is what makes the bot read the
# prefetched $LOCAT_MODEL_DIR/huggingface weights offline.
import config

from dotenv import load_dotenv
from loguru import logger
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.workers.runner import WorkerRunner

if TYPE_CHECKING:  # import for typing only — see build_transport() for why
    from pipecat.transports.local.audio import LocalAudioTransport

# Pipeline assembly (VAD/STT/LLM/TTS/context) lives in pipeline.py, shared by all bots.
from pipeline import build_pipeline


def build_transport() -> "LocalAudioTransport":
    """Build the local audio transport (mic in + speaker out).

    - Device indices come from config (LOCAT_INPUT_DEVICE_INDEX / LOCAT_OUTPUT_DEVICE_INDEX;
      default: the system default input/output devices).

    PyAudio is imported here rather than at module scope on purpose. It is an
    opt-in extra (`local-audio`) because it has no macOS/Linux wheels and must
    compile against PortAudio — and bot_web.py / bot_moq.py import this module
    for its builders while getting audio from the browser instead. A top-level
    import would break those front-ends on machines without PortAudio, and would
    also give this module the import-time side effect its docstring disclaims.
    """
    try:
        from pipecat.transports.local.audio import (
            LocalAudioTransport,
            LocalAudioTransportParams,
        )
    except ImportError as e:
        # Pipecat logs its own hint here pointing at `pipecat-ai[local]`, which
        # is not how this repo installs it — override with the real command.
        sys.exit(
            f"bot.py needs PyAudio for mic/speaker access ({e}).\n"
            "  macOS:  brew install portaudio\n"
            "  Debian: sudo apt install portaudio19-dev\n"
            "  then:   uv sync --extra local-audio\n"
            "\n"
            "Or skip it and use a browser front-end, which needs no PortAudio and\n"
            "gives you echo cancellation for free:  ./start.sh  (or: ./start.sh -t moq)"
        )

    params = LocalAudioTransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        input_device_index=config.input_device_index(),
        output_device_index=config.output_device_index(),
    )
    return LocalAudioTransport(params)


async def _speak_greeting(worker: PipelineWorker) -> None:
    """Speak a short opening line shortly after the pipeline starts.

    `LocalAudioTransport` does NOT emit an `on_client_connected` event — that
    event only fires on networked transports (WebSocket/WebRTC/Daily/etc.), so
    hooking it here would silently never run. Instead, mirroring Pipecat's own
    `getting-started/01a-local-audio.py`, we wait briefly for the audio output
    stream to come up, then queue a `TTSSpeakFrame`.
    """
    await asyncio.sleep(config.greeting_delay_secs())
    await worker.queue_frames([TTSSpeakFrame(config.greeting())])


def _configure_logging() -> None:
    """Route Pipecat's loguru output to stderr at LOCAT_LOG_LEVEL (default DEBUG).
    """
    logger.remove()
    logger.add(sys.stderr, level=config.log_level())


def _preflight_llm(model: str, base_url: str) -> None:
    """Fail fast with a clear message if the local LLM isn't usable.

    A missing Ollama server or un-pulled model otherwise fails silently mid-turn
    (the LLM call errors and nothing is spoken), which is confusing.
    """
    url = base_url.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 (localhost)
            models = json.load(resp).get("data", [])
    except (urllib.error.URLError, OSError) as exc:
        sys.exit(
            f"\n✖ Cannot reach the local LLM server at {base_url}\n"
            f"  Start Ollama first:  ./scripts/run_ollama.sh   (or: ollama serve)\n"
            f"  Details: {exc}\n"
        )

    available = {str(m.get("id", "")) for m in models}
    # Ollama reports tags like 'llama3:latest'; match the bare name too.
    if not any(model == m or model.split(":")[0] == m.split(":")[0] for m in available):
        listed = ", ".join(sorted(available)) or "(none)"
        sys.exit(
            f"\n✖ LLM model '{model}' is not available in Ollama at {base_url}\n"
            f"  Pull it:            ollama pull {model}\n"
            f"  Or set LOCAT_LLM_MODEL in .env to one you have: {listed}\n"
        )
    logger.info(f"LLM preflight OK: '{model}' available at {base_url}")


async def main() -> None:
    """`uv run bot.py` entry point. It loads `.env` (config only — no
        secrets), assembles the pipeline, and hands the worker to a `WorkerRunner`,
        which manages the asyncio lifecycle and SIGINT/SIGTERM shutdown.
    """
    load_dotenv(override=True)
    _configure_logging()

    # Fail fast (with guidance) if the local LLM server/model isn't ready, rather
    # than silently producing no spoken reply when the first turn hits the LLM.
    _preflight_llm(config.llm_model(), config.ollama_base_url())

    transport = build_transport()
    built = build_pipeline(transport)

    worker = PipelineWorker(built.pipeline, params=PipelineParams())

    runner = WorkerRunner(handle_sigint=sys.platform != "win32")

    await runner.add_workers(worker)
    await asyncio.gather(runner.run(), _speak_greeting(worker))


if __name__ == "__main__":
    asyncio.run(main())
