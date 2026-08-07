"""Fully-offline Pipecat voice bot — main pipeline entry point.

Run with: `uv run bot.py`

This module is built incrementally across the Ralph phases. Phase 3 assembles the
full-duplex pipeline:

    transport.input() -> STT -> user-context -> LLM -> TTS -> transport.output() -> assistant-context

Everything runs locally: LocalAudioTransport (mic + speakers), Silero VAD + Local
Smart Turn v3 for turn-taking, and the STT/LLM/TTS engines built by services.py
(defaults: Whisper-MLX, Ollama, Kokoro — swappable via STT_ENGINE / TTS_ENGINE in
.env or ./doctor.sh -i). No cloud services, no API keys.

Importing this module has no side effects (no audio device access, no model loads) —
all hardware/model construction happens inside the builder functions and `main()`.
"""

import asyncio
import json
import sys
import urllib.error
import urllib.request

# Import config FIRST — before any pipecat/HF import. config sets HF_HOME (and the
# Kokoro cache paths) at import time, and Hugging Face freezes its cache root when
# huggingface_hub is imported, so this ordering is what makes the bot read the
# prefetched ./models/huggingface weights offline.
import config

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.workers.runner import WorkerRunner
from pipecat.transports.local.audio import (
    LocalAudioTransport,
    LocalAudioTransportParams,
)

from prompts.financial_advisor import SYSTEM_PROMPT

# STT / LLM / TTS construction lives in services.py, dispatched on the
# STT_ENGINE / TTS_ENGINE env vars — swap engines via .env (or ./doctor.sh -i)
# without touching this file.
from services import build_llm, build_stt, build_tts  # noqa: F401  (re-exported)


def build_transport() -> LocalAudioTransport:
    """Build the local audio transport (mic in + speaker out).

    - Device indices come from config (INPUT_DEVICE_INDEX / OUTPUT_DEVICE_INDEX;
      default: the system default input/output devices).
    """
    params = LocalAudioTransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        input_device_index=config.input_device_index(),
        output_device_index=config.output_device_index(),
    )
    return LocalAudioTransport(params)


def build_vad_processor() -> VADProcessor:
    """Build the Silero VAD pipeline processor (Pipecat 1.5 turn-taking source).
    """
    return VADProcessor(
        vad_analyzer=SileroVADAnalyzer(
            params=VADParams(
                confidence=config.vad_confidence(),
                min_volume=config.vad_min_volume(),
                start_secs=config.vad_start_secs(),
                stop_secs=config.vad_stop_secs(),
            )
        )
    )

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
    """Route Pipecat's loguru output to stderr at LOG_LEVEL (default DEBUG).
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
            f"  Or set LLM_MODEL in .env to one you have: {listed}\n"
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
    vad = build_vad_processor()
    stt = build_stt()
    llm = build_llm()
    tts = build_tts()
    context = LLMContext(messages=[{"role": "system", "content": SYSTEM_PROMPT}])
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(context)

    pipeline = Pipeline(
        [
            transport.input(),
            vad,
            stt,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(pipeline, params=PipelineParams())

    runner = WorkerRunner(handle_sigint=sys.platform != "win32")

    await runner.add_workers(worker)
    await asyncio.gather(runner.run(), _speak_greeting(worker))


if __name__ == "__main__":
    asyncio.run(main())
