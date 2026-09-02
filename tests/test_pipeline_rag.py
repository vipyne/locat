from pathlib import Path

import pipeline
from rag import FakeEmbedder, OllamaEmbedder, index
from rag_processor import RAGProcessor

FIXTURES = Path(__file__).parent / "fixtures"


def test_no_index_builds_no_rag_processor(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAT_RAG_INDEX_DIR", str(tmp_path / "missing"))
    assert pipeline.build_rag_processor() is None


def test_existing_index_builds_rag_processor(monkeypatch, tmp_path):
    index_dir = tmp_path / "rag-index"
    index(FIXTURES, index_dir, FakeEmbedder())
    monkeypatch.setenv("LOCAT_RAG_INDEX_DIR", str(index_dir))
    monkeypatch.setenv("LOCAT_RAG_TOP_K", "2")
    monkeypatch.setenv("LOCAT_EMBED_MODEL", "test-embed")

    processor = pipeline.build_rag_processor()

    assert isinstance(processor, RAGProcessor)
    assert isinstance(processor._embedder, OllamaEmbedder)
    assert processor._embedder.model == "test-embed"
    assert processor._top_k == 2
    assert processor._index_dir == index_dir
