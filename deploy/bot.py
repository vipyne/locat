"""deploy/bot.py — the CLOUD variant of the bot, for Pipecat Cloud (Daily transport).

This is a SEPARATE entry point from the local bots. It exists because a Pipecat Cloud
agent runs in a **Linux ARM64, CPU, "keep it small"** container — which forces three
changes from the local offline bot. Everything here is annotated with *why*.

  1. Transport  — there's no mic/speaker in a container, so LocalAudioTransport is out.
                  Pipecat Cloud sessions are Daily WebRTC. We use `create_transport`
                  with a "daily" entry (same pattern bot_web.py/bot_moq.py use).
  2. STT        — WhisperSTTServiceMLX is Apple-Silicon/MLX ONLY; it won't even import
                  on Linux. We use faster-whisper (`WhisperSTTService`) on CPU instead.
  3. LLM        — we do NOT run Ollama in this container (no GPU, 9 GB model). We point
                  at a REMOTE OpenAI-compatible endpoint via OLLAMA_BASE_URL. Because
                  OLLamaLLMService is just OpenAILLMService underneath, that's config,
                  not code. (See deploy/README.md for the two ways to host it.)

Cross-platform pieces are reused from the repo: `SpokenTextFilter` (money→words) and the
financial system prompt. We deliberately do NOT import the local `config.py` (it steers
model caches into ./models/ for the *local* repo layout; in the container we let the HF
cache default and download on cold start).

Entry point: `async def bot(runner_args)` — the name the Pipecat Cloud base image discovers.
"""

import os

from loguru import logger
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.ollama.llm import OLLamaLLMService
from pipecat.services.whisper.stt import Model, WhisperSTTService  # faster-whisper (cross-platform)
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.daily.transport import DailyParams

# Cross-platform, side-effect-free reuse from the repo (copied into the image by the Dockerfile).
from spoken_text_filter import SpokenTextFilter
from prompts.financial_advisor import SYSTEM_PROMPT


# --- config from the environment (injected as Pipecat Cloud secrets) --------------------
# REQUIRED: where your remote LLM lives. See deploy/README.md for the two hosting flavors.
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "")  # bearer token if your endpoint is authed
LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5:14b")
# faster-whisper model on a CPU agent — keep it SMALL for latency (tiny/base/small).
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
KOKORO_VOICE = os.getenv("KOKORO_VOICE", "af_heart")
GREETING = os.getenv(
    "GREETING",
    "Hi. I'm your private financial thinking partner. What's on your mind today?",
)

# Daily WebRTC transport. No ICE/keys needed here — Pipecat Cloud + the runner provision
# the room; we just declare we want audio in/out.
transport_params = {
    "daily": lambda: DailyParams(audio_in_enabled=True, audio_out_enabled=True),
}


def _build_stt() -> WhisperSTTService:
    """faster-whisper on CPU. Note: large models are slow on the agent-1x profile —
    prefer a small model here, or run STT on your GPU host alongside the LLM."""
    return WhisperSTTService(
        device="cpu",
        settings=WhisperSTTService.Settings(model=Model[WHISPER_MODEL.upper()]),
    )


def _build_llm() -> OLLamaLLMService:
    """Remote OpenAI-compatible LLM. OLLamaLLMService = OpenAILLMService, so pointing it
    at any /v1 endpoint (remote Ollama or vLLM) + an api_key is all it takes."""
    return OLLamaLLMService(
        settings=OLLamaLLMService.Settings(model=LLM_MODEL),
        base_url=OLLAMA_BASE_URL,
        api_key=OLLAMA_API_KEY or "ollama",  # OpenAI client requires a non-empty key
    )


def _build_tts() -> KokoroTTSService:
    """Kokoro is ONNX → runs on Linux CPU unchanged. Reuse the money/percent filter."""
    return KokoroTTSService(
        settings=KokoroTTSService.Settings(voice=KOKORO_VOICE),
        text_filters=[SpokenTextFilter()],
    )


def _build_vad() -> VADProcessor:
    """Silero VAD as a pipeline processor (drives segmented STT + smart-turn), ONNX/CPU."""
    return VADProcessor(vad_analyzer=SileroVADAnalyzer(params=VADParams(min_volume=0.0)))


async def run_bot(transport: BaseTransport) -> None:
    """Same pipeline shape as the local bot — only STT + transport differ."""
    stt = _build_stt()
    llm = _build_llm()
    tts = _build_tts()
    context = LLMContext(messages=[{"role": "system", "content": SYSTEM_PROMPT}])
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(context)

    pipeline = Pipeline(
        [
            transport.input(),
            _build_vad(),
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
        logger.info("Client connected — greeting")
        await worker.queue_frames([TTSSpeakFrame(GREETING)])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        logger.info("Client disconnected")
        await worker.cancel()

    from pipecat.workers.runner import WorkerRunner

    runner = WorkerRunner(handle_sigint=False)  # the cloud base image manages lifecycle
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments) -> None:
    """Entry point the Pipecat Cloud base image discovers and calls per session."""
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport)
