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
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.ollama.llm import OLLamaLLMService
from pipecat.services.whisper.stt import MLXModel, WhisperSTTServiceMLX
from pipecat.transports.local.audio import (
    LocalAudioTransport,
    LocalAudioTransportParams,
)

from prompts.financial_advisor import SYSTEM_PROMPT
from spoken_text_filter import SpokenTextFilter


def build_transport() -> LocalAudioTransport:
    """Build the local audio transport (mic in + speaker out).

    - Device indices come from config (INPUT_DEVICE_INDEX / OUTPUT_DEVICE_INDEX;
      default: the system default input/output devices).

    IMPORTANT (Pipecat 1.5 architecture): VAD and turn-taking are NOT configured
    on the transport. `TransportParams` has no `vad_analyzer`/`turn_analyzer`
    fields, and (because Pydantic ignores unknown kwargs here) passing them is
    silently dropped — the classic failure where the bot hears nothing. VAD is a
    pipeline processor (`VADProcessor`, see `build_vad_processor`) placed right
    after `transport.input()`; smart-turn is applied automatically by the LLM
    user aggregator's default turn strategies (see `build_context_aggregator`).
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

    Placed immediately after `transport.input()`, this emits
    `VADUserStartedSpeakingFrame` / `VADUserStoppedSpeakingFrame` downstream. Those
    frames drive three things: the segmented Whisper STT (which transcribes a
    segment on speech-stop), barge-in interruptions, and the user aggregator's
    turn strategies (including local Smart Turn v3, applied by default).

    VAD tuning matters for portability: Pipecat gates speech on Silero's neural
    confidence AND an absolute EBU-R128 loudness (`min_volume`). The loudness gate
    is level-sensitive, so its stock 0.6 default makes quiet mics (and different
    machines) fail to register speech. We drive `VADParams` from config, defaulting
    `min_volume=0.0` (gate off) so turn detection is level-independent and works
    across mics without per-machine calibration. See `config.vad_*` / `.env`.
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
        # Deterministically speak money/percent/large numbers ("$3,000" ->
        # "three thousand dollars") instead of relying on the LLM to spell them
        # out — small local models won't do it reliably. See spoken_text_filter.py.
        text_filters=[SpokenTextFilter()],
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


def build_pipeline_task() -> tuple[LocalAudioTransport, PipelineWorker]:
    """Assemble the full-duplex pipeline and wrap it in a `PipelineWorker`.

    Frame flow (the Pipecat 1.5 universal-context ordering)::

        transport.input() -> VADProcessor -> STT -> user-aggregator -> LLM -> TTS
            -> transport.output() -> assistant-aggregator

    The `VADProcessor` sits right after the input and emits speech start/stop
    frames. Those drive the segmented Whisper STT (transcribe on stop), barge-in
    interruptions, and the user aggregator's turn strategies (local Smart Turn v3
    by default). The user aggregator folds finalized transcriptions into the shared
    context before the LLM sees them; the assistant aggregator folds the bot's
    spoken reply back in after TTS. All services are the local/offline builders.

    Why VAD is its own processor (not a transport param): in Pipecat 1.5 the local
    transport does NOT accept `vad_analyzer`/`turn_analyzer`; VAD lives in the
    pipeline. Interruptions are on by default once VAD frames flow, so we pass a
    plain `PipelineParams()`.

    The transport is returned alongside the worker so callers can register
    transport event handlers / queue the on-ready greeting.
    """
    transport = build_transport()
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
    return transport, worker


async def _speak_greeting(worker: PipelineWorker) -> None:
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
    await worker.queue_frames([TTSSpeakFrame(config.greeting())])


def _configure_logging() -> None:
    """Route Pipecat's loguru output to stderr at LOG_LEVEL (default DEBUG).

    DEBUG is a sensible default here: it surfaces each service's activity, which
    is exactly what you want to watch during the offline verification (Phase 6)
    to confirm no service silently reaches the network.
    """
    logger.remove()
    logger.add(sys.stderr, level=config.log_level())


def _preflight_llm(model: str, base_url: str) -> None:
    """Fail fast with a clear message if the local LLM isn't usable.

    A missing Ollama server or un-pulled model otherwise fails silently mid-turn
    (the LLM call errors and nothing is spoken), which is confusing. We check up
    front against the OpenAI-compatible `/models` endpoint and exit with actionable
    guidance if the server is unreachable or the configured model isn't present.
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
    """Build and run the offline voice bot until interrupted (Ctrl-C / EOF).

    This is the `uv run bot.py` entry point. It loads `.env` (config only — no
    secrets), assembles the pipeline, and hands the worker to a `WorkerRunner`,
    which manages the asyncio lifecycle and SIGINT/SIGTERM shutdown.
    """
    load_dotenv(override=True)
    _configure_logging()

    # Fail fast (with guidance) if the local LLM server/model isn't ready, rather
    # than silently producing no spoken reply when the first turn hits the LLM.
    _preflight_llm(config.llm_model(), config.ollama_base_url())

    _transport, worker = build_pipeline_task()

    # handle_sigint is unsupported on Windows event loops; guard it.
    runner = WorkerRunner(handle_sigint=sys.platform != "win32")

    # Register the worker, then run the runner and the on-startup greeting
    # concurrently: the greeting coroutine waits for the audio output stream to
    # come up, then queues the opening line. (LocalAudioTransport has no ready
    # event to hook — see `_speak_greeting`.) Passing the worker straight to
    # `run()` still works but is deprecated since 1.3.0; `add_workers()` then
    # `run()` is the current 1.5 API.
    await runner.add_workers(worker)
    await asyncio.gather(runner.run(), _speak_greeting(worker))


if __name__ == "__main__":
    asyncio.run(main())
