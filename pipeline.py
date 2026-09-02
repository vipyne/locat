"""Shared pipeline construction — one brain, every transport.

`build_pipeline(transport)` assembles the full-duplex pipeline used by every bot:

    transport.input() -> VAD -> STT -> user-context -> LLM -> TTS -> transport.output() -> assistant-context

Bot files keep only what differs per transport: transport construction, preflight,
greeting, and runner glue.
"""

from dataclasses import dataclass

# config FIRST — sets HF_HOME / Kokoro cache paths before any pipecat/HF import (see config.py).
import config

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMAssistantAggregator,
    LLMContextAggregatorPair,
    LLMUserAggregator,
)
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.transports.base_transport import BaseTransport

from prompts.financial_advisor import SYSTEM_PROMPT
from services import build_llm, build_stt, build_tts


@dataclass
class BuiltBot:
    pipeline: Pipeline
    context: LLMContext
    user_aggregator: LLMUserAggregator
    assistant_aggregator: LLMAssistantAggregator


def build_vad_processor() -> VADProcessor:
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


def build_pipeline(transport: BaseTransport) -> BuiltBot:
    context = LLMContext(messages=[{"role": "system", "content": SYSTEM_PROMPT}])
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(context)

    pipeline = Pipeline(
        [
            transport.input(),
            build_vad_processor(),
            build_stt(),
            user_aggregator,
            build_llm(),
            build_tts(),
            transport.output(),
            assistant_aggregator,
        ]
    )
    return BuiltBot(
        pipeline=pipeline,
        context=context,
        user_aggregator=user_aggregator,
        assistant_aggregator=assistant_aggregator,
    )
