"""Browser voice bot over MoQ (Media over QUIC) — lower latency than WebRTC, same brain.

Like bot_web.py this serves the bot to a browser (so echo cancellation is free from the
browser's getUserMedia), but over MoQ/QUIC instead of WebRTC — lower latency, and it pairs
with the terminal-styled voice-ui-kit console we want for the frontend. The bot runs its own
MoQ relay in serve mode; the runner auto-generates a localhost TLS cert, so it stays fully
offline (loopback QUIC — no internet).

Run:
    ./start.sh
    # open http://localhost:7860, choose "Media over QUIC" in the dropdown, allow the mic, Connect.

Reuses bot.py's exact pipeline/builders (VADProcessor + Whisper/Ollama/Kokoro + SpokenTextFilter);
the ONLY difference from bot_web.py is the transport (MOQParams) and MoQ's event-handler shapes.
"""

# config FIRST — sets HF_HOME / Kokoro cache paths before any pipecat/HF import (see config.py).
import config

from dotenv import load_dotenv
from loguru import logger
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.moq.transport import MOQParams
from pipecat.workers.runner import WorkerRunner
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)

from prompts.financial_advisor import SYSTEM_PROMPT

# Reuse the exact offline builders + helpers from the CLI bot — one brain, three transports.
from bot import (
    _configure_logging,
    _preflight_llm,
    build_vad_processor,
)

# STT/LLM/TTS come from services.py (engine choice via STT_ENGINE / TTS_ENGINE).
from services import build_llm, build_stt, build_tts

load_dotenv(override=True)

# MoQ transport with audio in/out. Serve mode (the bot is its own MoQ relay)
transport_params = {
    "moq": lambda: MOQParams(audio_in_enabled=True, audio_out_enabled=True),
}


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    """Assemble and run the pipeline for one connected browser client (over MoQ).
    """
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

    # NOTE: MoQ's handlers differ from SmallWebRTC's — they receive only the transport
    # (no client argument).
    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport):
        logger.info("MoQ client subscribed — greeting")
        await worker.queue_frames([TTSSpeakFrame(config.greeting())])

    # A browser tab closing surfaces as the peer's broadcast going away
    # (on_client_disconnected, new in Pipecat 1.7); the whole MoQ session ending
    # fires on_disconnected. End the bot session on either.
    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport):
        logger.info("MoQ client disconnected — ending session")
        await worker.cancel()

    @transport.event_handler("on_disconnected")
    async def on_disconnected(_transport):
        logger.info("MoQ session ended — ending session")
        await worker.cancel()

    @transport.event_handler("on_error")
    async def on_error(_transport, message, _exception):
        logger.error(f"MoQ error: {message}")

    # MOQInputTransport auto-connects to the relay when the pipeline starts, so we don't
    # dial transport.connect() here; we disconnect explicitly on shutdown.
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    try:
        await runner.add_workers(worker)
        await runner.run()
    finally:
        await transport.disconnect()


async def bot(runner_args: RunnerArguments) -> None:
    """Entry point the Pipecat dev runner discovers and calls per connection."""
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    _configure_logging()
    _preflight_llm(config.llm_model(), config.ollama_base_url())

    from pipecat.runner.run import main

    main()
