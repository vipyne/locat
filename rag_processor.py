"""Always-on retrieval between the user aggregator and the LLM.

On each LLMContextFrame: embed the latest user message, retrieve the top-k
document chunks, and keep exactly one excerpts system message in the context
(the previous turn's is replaced). No index or no results → frames pass
through untouched.
"""

import asyncio
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import Frame, LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext, LLMContextMessage
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

import rag

EXCERPTS_HEADER = (
    "Relevant excerpts from the user's documents (cite the file name when you use one):"
)


def _is_excerpts_message(message: LLMContextMessage) -> bool:
    return (
        isinstance(message, dict)
        and message.get("role") == "system"
        and isinstance(message.get("content"), str)
        and message["content"].startswith(EXCERPTS_HEADER)
    )


def _latest_user_text(context: LLMContext) -> str:
    for message in reversed(context.messages):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
    return ""


def _locate(chunk: rag.Chunk) -> str:
    return chunk.source_path if chunk.page is None else f"{chunk.source_path} p.{chunk.page}"


class RAGProcessor(FrameProcessor):
    def __init__(self, index_dir: Path, embedder: rag.Embedder, top_k: int):
        super().__init__()
        self._index_dir = index_dir
        self._embedder = embedder
        self._top_k = top_k

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            await self._inject_excerpts(frame.context)
        await self.push_frame(frame, direction)

    async def _inject_excerpts(self, context: LLMContext):
        query = _latest_user_text(context)
        if not query:
            return
        chunks = await asyncio.to_thread(
            rag.retrieve, query, self._top_k, self._index_dir, self._embedder
        )
        if not chunks:
            return
        for chunk in chunks:
            logger.info(f"rag: injecting {_locate(chunk)} (score {chunk.score:.3f})")
        messages = [m for m in context.messages if not _is_excerpts_message(m)]
        messages.append(
            {
                "role": "system",
                "content": "\n".join(
                    [EXCERPTS_HEADER, *(f"[{_locate(c)}] {c.text}" for c in chunks)]
                ),
            }
        )
        context.set_messages(messages)
