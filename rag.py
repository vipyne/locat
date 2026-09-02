"""Offline RAG over the user's documents. Bot code only calls index() / retrieve()."""

import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from pypdf import PdfReader


@dataclass
class PageText:
    text: str
    source_path: str
    page: int | None


def extract(path: Path) -> list[PageText]:
    source_path = str(path.resolve())
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        return [PageText(text=path.read_text(), source_path=source_path, page=None)]
    if suffix == ".pdf":
        return [
            PageText(text=page.extract_text(), source_path=source_path, page=number)
            for number, page in enumerate(PdfReader(path).pages, start=1)
        ]
    logger.info(f"skipping {source_path}: unsupported extension")
    return []


def chunk_text(text: str, chunk_tokens: int = 500, overlap: int = 50) -> list[str]:
    """Pack sentences into chunks of at most chunk_tokens words (a "token" is a
    whitespace-split word), each chunk repeating the previous chunk's last
    `overlap` words."""
    words = text.split()
    if not words:
        return []
    if len(words) <= chunk_tokens:
        return [" ".join(words)]

    sentences = [s.split() for s in re.split(r"(?<=[.!?])\s", text)]
    sentences = [s for s in sentences if s]

    chunks: list[list[str]] = []
    current: list[str] = []

    def flush_and_carry_overlap() -> list[str]:
        chunks.append(current)
        return current[-overlap:] if overlap else []

    for sentence in sentences:
        if len(current) + len(sentence) <= chunk_tokens:
            current += sentence
        elif len(sentence) > chunk_tokens:
            for word in sentence:
                if len(current) >= chunk_tokens:
                    current = flush_and_carry_overlap()
                current.append(word)
        else:
            current = flush_and_carry_overlap()
            room = chunk_tokens - len(sentence)
            current = current[-room:] if room else []
            current += sentence
    if current:
        chunks.append(current)
    return [" ".join(c) for c in chunks]
