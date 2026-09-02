from pathlib import Path

from rag import chunk_text, extract

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
