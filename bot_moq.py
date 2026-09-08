"""Browser voice bot over MoQ (Media over QUIC) — lower latency than WebRTC, same brain.

Like bot_web.py this serves the bot to a browser (so echo cancellation is free from the
browser's getUserMedia), but over MoQ/QUIC instead of WebRTC — lower latency, and it pairs
with the terminal-styled voice-ui-kit console we want for the frontend. The bot runs its own
MoQ relay in serve mode; the runner auto-generates a localhost TLS cert, so it stays fully
offline (loopback QUIC — no internet).

Run:
    ./locat.sh start
    # opens http://localhost:7860 (the locat client from client/dist/) — allow the mic, Connect.

Reuses the shared pipeline from pipeline.py (VADProcessor + Whisper/Ollama/Kokoro);
the ONLY difference from bot_web.py is the transport (MOQParams) and MoQ's event-handler shapes.
"""

# config FIRST — sets HF_HOME / Kokoro cache paths before any pipecat/HF import (see config.py).
import config

import asyncio
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.moq.transport import MOQParams
from pipecat.workers.runner import WorkerRunner

import rag
from bot import _configure_logging, _preflight_llm, _preflight_rag, _preflight_stt
from pipeline import build_pipeline
from scripts.print_models import model_entries

load_dotenv(override=True)

CONFIG_MESSAGE_TYPE = "locat-config"

CLIENT_DIST = Path(__file__).resolve().parent / "client" / "dist"


def _serve_client_dist(app) -> None:
    """Register the locat frontend on the dev runner's FastAPI app.

    The runner has no option for a custom static dir (its `_setup_frontend_routes`
    hardcodes the pipecat-ai-prebuilt UI, now uninstalled — it logs one startup
    error about that and mounts nothing). Routes registered before `main()` win.
    """
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=CLIENT_DIST / "assets"), name="client-assets")

    @app.get("/", include_in_schema=False)
    async def client_index():
        return FileResponse(CLIENT_DIST / "index.html")


def _locat_config_message() -> dict:
    return {
        "type": CONFIG_MESSAGE_TYPE,
        "models": model_entries(),
        "ollama_host": config.ollama_base_url(),
        "rag": rag.stats_summary(Path(config.rag_index_dir()), config.rag_data_dir()),
    }

# MoQ transport with audio in/out. Serve mode (the bot is its own MoQ relay)
transport_params = {
    "moq": lambda: MOQParams(audio_in_enabled=True, audio_out_enabled=True),
}


async def wait_for_audio_subscriber(
    transport, client_ready: asyncio.Event, timeout: float
) -> bool:
    """Hold the greeting until the browser can actually hear it.

    MoQ is live media with no replay: audio written before the bot's audio
    track is open is dropped inside ``publish_audio``, and audio sent before
    the browser subscribes to that track is never delivered. The installed
    moq bindings (moq_rs 0.3.3) expose no subscriber signal — ``AudioProducer``
    has no ``used()`` — so this waits on the closest real signals instead:
    the track being open, plus the client's RTVI ``client-ready`` (sent only
    after the browser has wired up its audio pipeline). Returns False if
    either signal is still missing after ``timeout`` seconds.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while transport._client._audio_out is None:
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0.05)
    try:
        await asyncio.wait_for(client_ready.wait(), max(deadline - loop.time(), 0))
    except TimeoutError:
        return False
    return True


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    """Assemble and run the pipeline for one connected browser client (over MoQ).
    """
    built = build_pipeline(transport)
    worker = PipelineWorker(built.pipeline, params=PipelineParams())

    client_ready = asyncio.Event()

    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        client_ready.set()
        logger.info("RTVI client ready — sending locat-config")
        await rtvi.send_server_message(_locat_config_message())

    # NOTE: MoQ's handlers differ from SmallWebRTC's — they receive only the transport
    # (no client argument).
    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport):
        timeout = config.moq_subscriber_timeout()
        logger.info("MoQ client connected — waiting for its audio subscription")
        if not await wait_for_audio_subscriber(transport, client_ready, timeout):
            logger.warning(
                f"no audio subscriber confirmed after {timeout}s — greeting may be clipped"
            )
        logger.info("greeting")
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
    _preflight_stt()
    _preflight_rag()

    from pipecat.runner.run import app, main

    _serve_client_dist(app)
    main()
