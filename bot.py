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

import os

from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.services.whisper.stt import MLXModel, WhisperSTTServiceMLX
from pipecat.transports.local.audio import (
    LocalAudioTransport,
    LocalAudioTransportParams,
)


def _env_device_index(name: str) -> int | None:
    """Read a PyAudio device index from the environment.

    Returns None (use the system default device) when the var is unset or blank.
    Phase 4 will move this into config.py; kept inline here so the transport task
    stands alone.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    return int(raw)


def build_transport() -> LocalAudioTransport:
    """Build the local audio transport with VAD + smart-turn turn-taking.

    - Silero VAD detects speech vs. silence (enables barge-in / interruptions).
    - Local Smart Turn v3 decides when the user has actually finished their turn.
      Both models are bundled with pipecat and load from local ONNX — no network.
    - Device indices come from INPUT_DEVICE_INDEX / OUTPUT_DEVICE_INDEX (default:
      the system default input/output devices).
    """
    params = LocalAudioTransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        input_device_index=_env_device_index("INPUT_DEVICE_INDEX"),
        output_device_index=_env_device_index("OUTPUT_DEVICE_INDEX"),
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
    model_name = os.getenv("WHISPER_MODEL", "").strip() or "LARGE_V3_TURBO"
    model = MLXModel[model_name]
    return WhisperSTTServiceMLX(settings=WhisperSTTServiceMLX.Settings(model=model))
