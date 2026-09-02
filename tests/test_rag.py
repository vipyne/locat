import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from rag import FakeEmbedder, OllamaEmbedder, chunk_text, extract, has_index, index, retrieve

FIXTURES = Path(__file__).parent / "fixtures"


def make_sentences(count: int, words_each: int = 3) -> str:
    return " ".join(
        " ".join(f"s{i}w{j}" for j in range(words_each - 1)) + f" s{i}end."
        for i in range(count)
    )


def test_short_text_is_one_chunk():
    assert chunk_text("hi there") == ["hi there"]


def test_empty_text_is_no_chunks():
    assert chunk_text("   ") == []


def test_chunks_respect_budget():
    chunks = chunk_text(make_sentences(60), chunk_tokens=20, overlap=5)
    assert len(chunks) > 1
    assert all(len(c.split()) <= 20 for c in chunks)


def test_overlap_repeats_tail_words():
    chunks = chunk_text(make_sentences(60), chunk_tokens=20, overlap=5)
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.split()[:5] == prev.split()[-5:]


def test_all_words_survive_chunking():
    text = make_sentences(60)
    covered = set()
    for c in chunk_text(text, chunk_tokens=20, overlap=5):
        covered.update(c.split())
    assert covered == set(text.split())


def test_giant_sentence_hard_splits():
    text = " ".join(f"w{i}" for i in range(100))
    chunks = chunk_text(text, chunk_tokens=20, overlap=5)
    assert len(chunks) > 1
    assert all(len(c.split()) <= 20 for c in chunks)
    covered = set()
    for c in chunks:
        covered.update(c.split())
    assert covered == set(text.split())


def test_sentence_boundaries_preferred():
    chunks = chunk_text(make_sentences(60), chunk_tokens=20, overlap=0)
    assert all(c.endswith(".") for c in chunks)


def test_extract_txt_is_one_page():
    pages = extract(FIXTURES / "note.txt")
    assert len(pages) == 1
    assert "quokka" in pages[0].text
    assert pages[0].page is None
    assert Path(pages[0].source_path).is_absolute()


def test_extract_md_is_one_page():
    pages = extract(FIXTURES / "note.md")
    assert len(pages) == 1
    assert "capybara" in pages[0].text
    assert pages[0].page is None
    assert Path(pages[0].source_path).is_absolute()


def test_extract_pdf_pages_are_numbered_from_one():
    pages = extract(FIXTURES / "one_page.pdf")
    assert len(pages) == 1
    assert "axolotl" in pages[0].text
    assert pages[0].page == 1
    assert Path(pages[0].source_path).is_absolute()


def test_extract_unknown_extension_is_skipped(tmp_path):
    docx = tmp_path / "note.docx"
    docx.write_text("not a supported format")
    assert extract(docx) == []


def test_fake_embedder_shape_and_dtype():
    vectors = FakeEmbedder().embed(["hello", "world"])
    assert vectors.shape == (2, FakeEmbedder.dim)
    assert vectors.dtype == np.float32


def test_fake_embedder_is_deterministic():
    a = FakeEmbedder().embed(["hello", "world"])
    b = FakeEmbedder().embed(["hello", "world"])
    assert np.array_equal(a, b)


def test_fake_embedder_distinguishes_texts():
    vectors = FakeEmbedder().embed(["hello", "world"])
    assert not np.array_equal(vectors[0], vectors[1])


def test_fake_embedder_empty_input():
    assert FakeEmbedder().embed([]).shape == (0, FakeEmbedder.dim)


def test_ollama_embedder_request_and_response():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    embedder = OllamaEmbedder("http://localhost:11434", "nomic-embed-text", client=client)
    vectors = embedder.embed(["first text", "second text"])

    assert seen["url"] == "http://localhost:11434/api/embed"
    assert seen["body"] == {"model": "nomic-embed-text", "input": ["first text", "second text"]}
    assert vectors.dtype == np.float32
    assert vectors.shape == (2, 2)


def test_ollama_embedder_raises_on_http_error():
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(500, text="boom"))
    )
    embedder = OllamaEmbedder("http://localhost:11434", "nomic-embed-text", client=client)
    with pytest.raises(httpx.HTTPStatusError):
        embedder.embed(["text"])


def test_index_writes_aligned_files_and_stats(tmp_path):
    index_dir = tmp_path / "rag-index"
    stats = index(FIXTURES, index_dir, FakeEmbedder())

    lines = (index_dir / "chunks.jsonl").read_text().splitlines()
    embeddings = np.load(index_dir / "embeddings.npy")
    manifest = json.loads((index_dir / "manifest.json").read_text())

    assert stats.files == 3
    assert stats.chunks == len(lines) == embeddings.shape[0] > 0
    assert stats.embed_model == "fake"
    assert manifest["embed_model"] == "fake"
    assert manifest["chunk_tokens"] == 500 and manifest["overlap"] == 50
    assert len(manifest["files"]) == 3
    for path, digest in manifest["files"].items():
        assert Path(path).is_absolute()
        assert len(digest) == 64


def test_retrieve_finds_distinctive_chunk_at_top(tmp_path):
    index_dir = tmp_path / "rag-index"
    embedder = FakeEmbedder()
    index(FIXTURES, index_dir, embedder)

    query = " ".join((FIXTURES / "note.txt").read_text().split())
    chunks = retrieve(query, k=2, index_dir=index_dir, embedder=embedder)

    assert "quokka" in chunks[0].text
    assert chunks[0].source_path == str((FIXTURES / "note.txt").resolve())
    assert chunks[0].score == pytest.approx(1.0, abs=1e-5)
    assert chunks[0].score > chunks[1].score


def test_retrieve_missing_index_returns_empty(tmp_path):
    assert retrieve("anything", 4, tmp_path / "nope", FakeEmbedder()) == []


def test_retrieve_empty_index_returns_empty(tmp_path):
    empty_data = tmp_path / "data"
    empty_data.mkdir()
    index_dir = tmp_path / "rag-index"
    stats = index(empty_data, index_dir, FakeEmbedder())
    assert stats.files == 0 and stats.chunks == 0
    assert retrieve("anything", 4, index_dir, FakeEmbedder()) == []


def test_reindex_overwrites_and_invalidates_cache(tmp_path):
    index_dir = tmp_path / "rag-index"
    embedder = FakeEmbedder()
    index(FIXTURES, index_dir, embedder)
    retrieve("warm the cache", 4, index_dir, embedder)

    new_data = tmp_path / "data"
    new_data.mkdir()
    (new_data / "only.txt").write_text("A wombat digs a burrow.")
    stats = index(new_data, index_dir, embedder)

    assert stats.files == 1 and stats.chunks == 1
    chunks = retrieve("A wombat digs a burrow.", 4, index_dir, embedder)
    assert len(chunks) == 1
    assert "wombat" in chunks[0].text


def test_has_index_flips_after_indexing(tmp_path):
    index_dir = tmp_path / "rag-index"
    assert not has_index(index_dir)
    index(FIXTURES, index_dir, FakeEmbedder())
    assert has_index(index_dir)
