import asyncio
import json
from pathlib import Path

from pipecat.frames.frames import LLMContextFrame, TextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pipecat.tests.utils import run_test

from rag import FakeEmbedder, index
from rag_processor import EXCERPTS_HEADER, RAG_MESSAGE_TYPE, RAGProcessor

FIXTURES = Path(__file__).parent / "fixtures"

# FakeEmbedder vectors are seeded from exact text, so a query only tops the
# ranking when it equals a chunk verbatim — and chunk_text normalizes
# whitespace, so the queries must too.
NOTE_TXT_CHUNK = " ".join((FIXTURES / "note.txt").read_text().split())
NOTE_MD_CHUNK = " ".join((FIXTURES / "note.md").read_text().split())


def build_index(tmp_path: Path) -> Path:
    index_dir = tmp_path / "rag-index"
    index(FIXTURES, index_dir, FakeEmbedder())
    return index_dir


def send_context(
    processor: RAGProcessor, context: LLMContext, expect_rag_message: bool = True
) -> list:
    expected = [LLMContextFrame, RTVIServerMessageFrame] if expect_rag_message else [LLMContextFrame]
    down, _ = asyncio.run(
        run_test(
            processor,
            frames_to_send=[LLMContextFrame(context=context)],
            expected_down_frames=expected,
        )
    )
    return down


def excerpts_messages(context: LLMContext) -> list[dict]:
    return [
        m
        for m in context.messages
        if isinstance(m, dict)
        and m.get("role") == "system"
        and str(m.get("content", "")).startswith(EXCERPTS_HEADER)
    ]


def test_injects_excerpts_with_source_paths(tmp_path):
    processor = RAGProcessor(build_index(tmp_path), FakeEmbedder(), top_k=2)
    context = LLMContext(
        messages=[
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": NOTE_TXT_CHUNK},
        ]
    )
    send_context(processor, context)

    injected = excerpts_messages(context)
    assert len(injected) == 1
    assert "quokka" in injected[0]["content"]
    assert str((FIXTURES / "note.txt").resolve()) in injected[0]["content"]
    assert context.messages[0] == {"role": "system", "content": "be helpful"}


def test_excerpts_message_stays_singular_across_turns(tmp_path):
    index_dir = build_index(tmp_path)
    context = LLMContext(messages=[{"role": "user", "content": NOTE_TXT_CHUNK}])
    send_context(RAGProcessor(index_dir, FakeEmbedder(), top_k=1), context)
    context.add_message({"role": "user", "content": NOTE_MD_CHUNK})
    send_context(RAGProcessor(index_dir, FakeEmbedder(), top_k=1), context)

    injected = excerpts_messages(context)
    assert len(injected) == 1
    assert "capybara" in injected[0]["content"]
    assert "quokka" not in injected[0]["content"]


def test_passthrough_when_no_index(tmp_path):
    processor = RAGProcessor(tmp_path / "missing", FakeEmbedder(), top_k=4)
    context = LLMContext(messages=[{"role": "user", "content": "anything at all"}])
    send_context(processor, context, expect_rag_message=False)

    assert excerpts_messages(context) == []
    assert context.messages == [{"role": "user", "content": "anything at all"}]


def test_context_without_user_message_untouched(tmp_path):
    processor = RAGProcessor(build_index(tmp_path), FakeEmbedder(), top_k=2)
    context = LLMContext(messages=[{"role": "system", "content": "sys only"}])
    send_context(processor, context, expect_rag_message=False)

    assert context.messages == [{"role": "system", "content": "sys only"}]


def test_retrieval_emits_locat_rag_server_message(tmp_path):
    processor = RAGProcessor(build_index(tmp_path), FakeEmbedder(), top_k=2)
    context = LLMContext(messages=[{"role": "user", "content": NOTE_TXT_CHUNK}])
    down = send_context(processor, context)

    message = next(f for f in down if isinstance(f, RTVIServerMessageFrame)).data
    json.dumps(message)
    assert message["type"] == RAG_MESSAGE_TYPE
    assert message["query"] == NOTE_TXT_CHUNK
    assert len(message["chunks"]) == 2
    top = message["chunks"][0]
    assert top["source_path"] == str((FIXTURES / "note.txt").resolve())
    assert "quokka" in top["text"]
    assert isinstance(top["score"], float)
    assert top["page"] is None


def test_other_frames_pass_through(tmp_path):
    processor = RAGProcessor(build_index(tmp_path), FakeEmbedder(), top_k=2)
    asyncio.run(
        run_test(
            processor,
            frames_to_send=[TextFrame(text="hello")],
            expected_down_frames=[TextFrame],
        )
    )


def test_excerpts_inserted_before_latest_user_message_never_trailing(tmp_path):
    """A context ending with a system message makes small llama models echo a
    literal 'assistant' header before their reply (verified against llama3.2:1b)."""
    processor = RAGProcessor(build_index(tmp_path), FakeEmbedder(), top_k=2)
    context = LLMContext(
        messages=[
            {"role": "system", "content": "be helpful"},
            {"role": "assistant", "content": "hi there"},
            {"role": "user", "content": NOTE_TXT_CHUNK},
        ]
    )
    send_context(processor, context)

    roles = [m["role"] for m in context.messages if isinstance(m, dict)]
    assert roles[-1] == "user"
    excerpts_index = next(
        i for i, m in enumerate(context.messages) if m in excerpts_messages(context)
    )
    assert excerpts_index == len(context.messages) - 2
