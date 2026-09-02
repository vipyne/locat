"""Offline RAG over the user's documents. Bot code only calls index() / retrieve()."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx
import numpy as np
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


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray: ...


class OllamaEmbedder:
    """Embeds via the native Ollama API: POST {base_url}/api/embed."""

    def __init__(self, base_url: str, model: str, client: httpx.Client | None = None):
        self._url = f"{base_url.rstrip('/')}/api/embed"
        self._model = model
        self._client = client or httpx.Client(timeout=120.0)

    def embed(self, texts: list[str]) -> np.ndarray:
        response = self._client.post(
            self._url, json={"model": self._model, "input": texts}
        )
        response.raise_for_status()
        return np.asarray(response.json()["embeddings"], dtype=np.float32)


class FakeEmbedder:
    """Deterministic offline stand-in for tests: vector seeded from sha256(text)."""

    dim = 32

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        rows = []
        for text in texts:
            seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
            rows.append(np.random.default_rng(seed).standard_normal(self.dim))
        vectors = np.asarray(rows, dtype=np.float32)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


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
