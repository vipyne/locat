"""Shared pipeline construction — one brain, every transport.

`build_pipeline(transport)` assembles the full-duplex pipeline used by every bot:

    transport.input() -> VAD -> STT -> user-context -> [RAG] -> LLM -> TTS -> transport.output() -> assistant-context

The RAG stage appears only when an index exists (./locat.sh index-rag).

Bot files keep only what differs per transport: transport construction, preflight,
greeting, and runner glue.
"""

from dataclasses import dataclass
from pathlib import Path

# config FIRST — sets HF_HOME / Kokoro cache paths before any pipecat/HF import (see config.py).
import config

from loguru import logger
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

import rag
from prompts.financial_advisor import SYSTEM_PROMPT
from rag_processor import RAGProcessor
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


_missing_index_logged = False


def build_rag_processor() -> RAGProcessor | None:
    global _missing_index_logged
    index_dir = Path(config.rag_index_dir())
    if not rag.has_index(index_dir):
        if not _missing_index_logged:
            logger.info(f"no RAG index found at {index_dir} — run ./locat.sh index-rag")
            _missing_index_logged = True
        return None
    return RAGProcessor(
        index_dir=index_dir,
        embedder=rag.OllamaEmbedder(config.ollama_api_url(), config.embed_model()),
        top_k=config.rag_top_k(),
    )


def build_pipeline(transport: BaseTransport) -> BuiltBot:
    context = LLMContext(messages=[{"role": "system", "content": SYSTEM_PROMPT}])
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(context)

    processors = [
        transport.input(),
        build_vad_processor(),
        build_stt(),
        user_aggregator,
    ]
    rag_processor = build_rag_processor()
    if rag_processor is not None:
        processors.append(rag_processor)
    processors += [
        build_llm(),
        build_tts(),
        transport.output(),
        assistant_aggregator,
    ]
    pipeline = Pipeline(processors)
    return BuiltBot(
        pipeline=pipeline,
        context=context,
        user_aggregator=user_aggregator,
        assistant_aggregator=assistant_aggregator,
    )
