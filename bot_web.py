"""Browser voice bot — same offline brain as bot.py, but over local WebRTC.

Why this exists: `bot.py` uses raw local audio (PyAudio), which has NO acoustic echo
cancellation, so on speakers the bot hears itself and self-interrupts. A browser gets
AEC + AGC + noise-suppression for free from `getUserMedia({audio:{echoCancellation:true}})`
— the browser captures AND plays audio, so its echo canceller has the reference signal.
We keep everything else identical and just swap the transport to Pipecat's serverless
`SmallWebRTCTransport`, served to a local browser tab. With no ICE servers it uses
loopback host candidates, so it still runs **fully offline / airplane mode**.

Run it:
    ./start.sh -t webrtc           # brings up Ollama + serves the bot
    # then open  http://localhost:7860/client  in a browser, allow the mic, and talk.

Everything is reused from bot.py (STT/LLM/TTS/VAD/context/preflight); the ONLY change is
the transport. Segmented Whisper still needs the upstream `VADProcessor`, so the pipeline
shape matches bot.py exactly.
"""

# config FIRST — sets HF_HOME / Kokoro cache paths before any pipecat/HF import loads
# (see config.py). Importing bot below also imports config, but doing it here guarantees
# the ordering regardless of import mechanics.
import config

from dotenv import load_dotenv
from loguru import logger
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner

# Reuse the exact offline builders + helpers from the CLI bot — one brain, two transports.
from bot import (
    _configure_logging,
    _preflight_llm,
    build_context,
    build_context_aggregator,
    build_llm,
    build_stt,
    build_tts,
    build_vad_processor,
)

load_dotenv(override=True)

# One transport factory: local WebRTC with audio in/out. No ICE servers are configured,
# so the peer connection uses loopback host candidates and needs no internet.
transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


async def run_bot(transport: BaseTransport) -> None:
    """Assemble and run the pipeline for one connected browser client.

    Identical pipeline to bot.py — the VADProcessor sits upstream of the segmented
    Whisper STT (which transcribes on speech-stop); smart-turn comes from the user
    aggregator's default strategies. Only the transport differs.
    """
    vad = build_vad_processor()
    stt = build_stt()
    llm = build_llm()
    tts = build_tts()
    context = build_context()
    user_aggregator, assistant_aggregator = build_context_aggregator(context)

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

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client):
        # SmallWebRTC (unlike LocalAudioTransport) fires this, so we greet on connect.
        logger.info("Browser client connected — greeting")
        await worker.queue_frames([TTSSpeakFrame(config.greeting())])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        logger.info("Browser client disconnected — ending session")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)  # the dev runner owns signals
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments) -> None:
    """Entry point the Pipecat dev runner discovers and calls per connection."""
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport)


if __name__ == "__main__":
    # Fail fast if the local LLM isn't ready (same preflight as bot.py), then hand off
    # to the dev runner, which serves the prebuilt browser client + WebRTC signaling on
    # http://localhost:7860 (open /client).
    _configure_logging()
    _preflight_llm(config.llm_model(), config.ollama_base_url())

    from pipecat.runner.run import main

    main()
