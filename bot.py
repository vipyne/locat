"""Fully-offline Pipecat voice bot — main pipeline entry point.

Run with: `uv run bot.py`

This module is built incrementally across the Ralph phases. Phase 3 assembles the
full-duplex pipeline:

    transport.input() -> STT -> user-context -> LLM -> TTS -> transport.output() -> assistant-context

Everything runs locally: LocalAudioTransport (mic + speakers), Silero VAD + Local
Smart Turn v3 for turn-taking, Whisper-MLX STT, Ollama LLM, and Kokoro TTS. No cloud
services, no API keys.

Importing this module has no side effects (no audio device access, no model loads) —
all hardware/model construction happens inside the builder functions and `main()`.
"""

import asyncio
import sys

# Import config FIRST — before any pipecat/HF import. config sets HF_HOME (and the
# Kokoro cache paths) at import time, and Hugging Face freezes its cache root when
# huggingface_hub is imported, so this ordering is what makes the bot read the
# prefetched ./models/huggingface weights offline.
import config

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.ollama.llm import OLLamaLLMService
from pipecat.services.whisper.stt import MLXModel, WhisperSTTServiceMLX
from pipecat.transports.local.audio import (
    LocalAudioTransport,
    LocalAudioTransportParams,
)

from prompts.financial_advisor import SYSTEM_PROMPT


def build_transport() -> LocalAudioTransport:
    """Build the local audio transport with VAD + smart-turn turn-taking.

    - Silero VAD detects speech vs. silence (enables barge-in / interruptions).
    - Local Smart Turn v3 decides when the user has actually finished their turn.
      Both models are bundled with pipecat and load from local ONNX — no network.
    - Device indices come from config (INPUT_DEVICE_INDEX / OUTPUT_DEVICE_INDEX;
      default: the system default input/output devices).
    """
    params = LocalAudioTransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        input_device_index=config.input_device_index(),
        output_device_index=config.output_device_index(),
        vad_analyzer=SileroVADAnalyzer(),
        turn_analyzer=LocalSmartTurnAnalyzerV3(),
    )
    return LocalAudioTransport(params)


def build_stt() -> WhisperSTTServiceMLX:
    """Build the Whisper-MLX speech-to-text service.

    Apple-Silicon-optimized Whisper via MLX. The model is chosen by the
    WHISPER_MODEL env var, matched against the `MLXModel` enum member name
    (default LARGE_V3_TURBO — finetuned/pruned large-v3, much faster with only
    slightly lower accuracy; already prefetched to ./models/huggingface in
    Phase 2). Other members: TINY, MEDIUM, LARGE_V3.

    Uses the current `settings=WhisperSTTServiceMLX.Settings(model=...)` API;
    the bare `model=` constructor arg is deprecated as of Pipecat 0.0.105.

    Construction is cheap and offline: MLX Whisper loads the weights lazily on
    the first transcription (from HF_HOME), so no network happens here.
    """
    model = MLXModel[config.whisper_model()]
    return WhisperSTTServiceMLX(settings=WhisperSTTServiceMLX.Settings(model=model))


def build_llm() -> OLLamaLLMService:
    """Build the local Ollama large-language-model service.

    `OLLamaLLMService` extends `OpenAILLMService` and talks to a locally running
    Ollama server over its OpenAI-compatible endpoint — no API key, no cloud. The
    model tag (LLM_MODEL, default `qwen2.5:14b`) is the same string used by
    `scripts/run_ollama.sh` to `ollama pull`, so the bot and the pull agree.

    The endpoint comes from OLLAMA_BASE_URL (default `http://localhost:11434/v1`).
    Note the trailing `/v1`: this is the OpenAI-compatible path, not the native
    Ollama API root.

    Uses the current `settings=OLLamaLLMService.Settings(model=...)` API; the bare
    `model=` constructor arg is deprecated as of Pipecat 0.0.105. Construction does
    not contact the server — connection happens when the pipeline runs.
    """
    return OLLamaLLMService(
        settings=OLLamaLLMService.Settings(model=config.llm_model()),
        base_url=config.ollama_base_url(),
    )


def build_tts() -> KokoroTTSService:
    """Build the local Kokoro neural text-to-speech service.

    Kokoro runs fully offline via kokoro-onnx. The voice comes from KOKORO_VOICE
    (default `af_heart` — Kokoro's flagship American-English voice; the shipped
    `Settings.voice` default is `None`, which kokoro-onnx cannot synthesize, so we
    always supply an explicit id).

    `model_path` / `voices_path` are pinned into ./models/kokoro (overridable via
    KOKORO_MODEL_PATH / KOKORO_VOICES_PATH) — the exact files scripts/prefetch_models.py
    downloads, so a warmed-up repo runs with zero network. If the files are missing,
    `KokoroTTSService.__init__` downloads them on the spot (the one online step).

    Uses the current `settings=KokoroTTSService.Settings(voice=...)` API; the bare
    `voice_id=` constructor arg is deprecated as of Pipecat 0.0.105.
    """
    return KokoroTTSService(
        settings=KokoroTTSService.Settings(voice=config.kokoro_voice()),
        model_path=config.kokoro_model_path(),
        voices_path=config.kokoro_voices_path(),
    )


def build_context() -> LLMContext:
    """Build the universal LLM context, seeded with the system prompt.

    `LLMContext` is Pipecat's current provider-agnostic conversation store
    (`pipecat.processors.aggregators.llm_context`) — it holds the message
    history that the LLM service reads on each turn. We seed it with a single
    `system` message; user and assistant turns are appended at runtime by the
    aggregator pair (see `build_context_aggregator`).

    The system prompt is the tuned financial thinking-partner personality from
    `prompts/financial_advisor.py` (spoken-output-tuned, not-a-licensed-advisor,
    no real account access).
    """
    return LLMContext(messages=[{"role": "system", "content": SYSTEM_PROMPT}])


def build_context_aggregator(context: LLMContext) -> LLMContextAggregatorPair:
    """Build the user/assistant context-aggregator pair for `context`.

    `LLMContextAggregatorPair` yields two processors that bracket the LLM in the
    pipeline: the *user* aggregator (placed before the LLM) folds finalized STT
    transcriptions into the context, and the *assistant* aggregator (placed after
    TTS) folds the bot's spoken response back in. Access them via
    `pair.user()` / `pair.assistant()`, or unpack directly:
    `user, assistant = build_context_aggregator(context)`.

    Constructing the pair is cheap and offline — no model loads, no network.
    """
    return LLMContextAggregatorPair(context)


def build_pipeline_task() -> tuple[LocalAudioTransport, PipelineTask]:
    """Assemble the full-duplex pipeline and wrap it in a `PipelineTask`.

    Frame flow (the current Pipecat 1.x universal-context ordering)::

        transport.input() -> STT -> user-aggregator -> LLM -> TTS
            -> transport.output() -> assistant-aggregator

    The user aggregator folds finalized transcriptions into the shared context
    before the LLM sees them; the assistant aggregator folds the bot's spoken
    reply back in after TTS. All services are the local/offline builders above.

    Interruptions (barge-in) are ON BY DEFAULT in Pipecat 1.x — turn management
    lives in the user aggregator's turn strategies. The 0.0.x-era
    `PipelineParams(allow_interruptions=True)` flag was REMOVED in Pipecat 1.0
    (confirmed via the context hub 1.0 migration guide), so we intentionally do
    NOT pass it; `PipelineParams()` defaults are correct for a local voice bot.

    The transport is returned alongside the task so callers can register
    transport event handlers (e.g. the on-ready greeting — a later Phase 3 task).
    """
    transport = build_transport()
    stt = build_stt()
    llm = build_llm()
    tts = build_tts()
    context = build_context()
    user_aggregator, assistant_aggregator = build_context_aggregator(context)

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    task = PipelineTask(pipeline, params=PipelineParams())
    return transport, task


async def _speak_greeting(task: PipelineTask) -> None:
    """Speak a short opening line shortly after the pipeline starts.

    `LocalAudioTransport` does NOT emit an `on_client_connected` event — that
    event only fires on networked transports (WebSocket/WebRTC/Daily/etc.), so
    hooking it here would silently never run. Instead, mirroring Pipecat's own
    `getting-started/01a-local-audio.py`, we wait briefly for the audio output
    stream to come up, then queue a `TTSSpeakFrame`, which sends the greeting text
    straight to Kokoro TTS (no LLM round-trip needed for a fixed opening line).

    We intentionally do NOT queue an `EndFrame` after it (that example is a
    one-shot); this is a conversation, so the pipeline keeps running and listening.
    """
    await asyncio.sleep(config.greeting_delay_secs())
    await task.queue_frames([TTSSpeakFrame(config.greeting())])


def _configure_logging() -> None:
    """Route Pipecat's loguru output to stderr at LOG_LEVEL (default DEBUG).

    DEBUG is a sensible default here: it surfaces each service's activity, which
    is exactly what you want to watch during the offline verification (Phase 6)
    to confirm no service silently reaches the network.
    """
    logger.remove()
    logger.add(sys.stderr, level=config.log_level())


async def main() -> None:
    """Build and run the offline voice bot until interrupted (Ctrl-C / EOF).

    This is the `uv run bot.py` entry point. It loads `.env` (config only — no
    secrets), assembles the pipeline, and hands the task to a `PipelineRunner`,
    which manages the asyncio lifecycle and SIGINT/SIGTERM shutdown.
    """
    load_dotenv(override=True)
    _configure_logging()

    _transport, task = build_pipeline_task()

    # handle_sigint is unsupported on Windows event loops; guard it.
    runner = PipelineRunner(handle_sigint=sys.platform != "win32")

    # Run the pipeline and the on-startup greeting concurrently: the greeting
    # coroutine waits for the audio output stream to come up, then queues the
    # opening line. (LocalAudioTransport has no ready event to hook — see
    # `_speak_greeting`.)
    await asyncio.gather(runner.run(task), _speak_greeting(task))


if __name__ == "__main__":
    asyncio.run(main())
