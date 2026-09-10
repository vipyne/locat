"""Offline RAG over the user's documents. Bot code only calls index() / retrieve()."""

import csv
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from itertools import zip_longest
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
    if suffix == ".csv":
        return _extract_csv(path, source_path)
    logger.info(f"skipping {source_path}: unsupported extension")
    return []


def _extract_csv(path: Path, source_path: str) -> list[PageText]:
    """Each row becomes a self-describing 'header: value, …' sentence.

    Labeling every value keeps a row meaningful after chunking separates it
    from the header line, and the trailing period makes each row a sentence
    boundary so chunk_text never splits a row down the middle.
    """
    with path.open(newline="") as handle:
        rows = list(csv.reader(handle))
    if len(rows) < 2:
        return []
    header = rows[0]
    lines = []
    for row in rows[1:]:
        cells = [
            f"{name.strip()}: {value.strip()}" if name.strip() else value.strip()
            for name, value in zip_longest(header, row, fillvalue="")
            if value.strip()
        ]
        if cells:
            lines.append(", ".join(cells) + ".")
    if not lines:
        return []
    return [PageText(text="\n".join(lines), source_path=source_path, page=None)]


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


class OllamaEmbedder:
    """Embeds via the native Ollama API: POST {base_url}/api/embed."""

    def __init__(self, base_url: str, model: str, client: httpx.Client | None = None):
        self._url = f"{base_url.rstrip('/')}/api/embed"
        self.model = model
        self._client = client or httpx.Client(timeout=120.0)

    def embed(self, texts: list[str]) -> np.ndarray:
        response = self._client.post(
            self._url, json={"model": self.model, "input": texts}
        )
        response.raise_for_status()
        return np.asarray(response.json()["embeddings"], dtype=np.float32)


class FakeEmbedder:
    """Deterministic offline stand-in for tests: vector seeded from sha256(text)."""

    dim = 32
    model = "fake"

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


EMBED_BATCH = 64


@dataclass
class IndexStats:
    files: int
    chunks: int
    embed_model: str


@dataclass
class Chunk:
    text: str
    source_path: str
    page: int | None
    score: float


def index(
    data_dir: Path,
    index_dir: Path,
    embedder: Embedder,
    chunk_tokens: int = 500,
    overlap: int = 50,
) -> IndexStats:
    """Full rebuild: data_dir → chunks.jsonl + embeddings.npy + manifest.json."""
    records: list[dict] = []
    file_hashes: dict[str, str] = {}
    for path in sorted(p for p in data_dir.rglob("*") if p.is_file()):
        pages = extract(path)
        if not pages:
            continue
        file_hashes[pages[0].source_path] = hashlib.sha256(path.read_bytes()).hexdigest()
        for page in pages:
            for text in chunk_text(page.text, chunk_tokens, overlap):
                records.append(
                    {"text": text, "source_path": page.source_path, "page": page.page}
                )

    batches = [
        embedder.embed([r["text"] for r in records[i : i + EMBED_BATCH]])
        for i in range(0, len(records), EMBED_BATCH)
    ]
    embeddings = np.vstack(batches) if batches else np.zeros((0, 0), dtype=np.float32)

    index_dir.mkdir(parents=True, exist_ok=True)
    with open(index_dir / "chunks.jsonl", "w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    np.save(index_dir / "embeddings.npy", embeddings)
    (index_dir / "manifest.json").write_text(
        json.dumps(
            {
                "embed_model": embedder.model,
                "files": file_hashes,
                "chunk_tokens": chunk_tokens,
                "overlap": overlap,
            },
            indent=2,
        )
    )
    return IndexStats(files=len(file_hashes), chunks=len(records), embed_model=embedder.model)


def has_index(index_dir: Path) -> bool:
    return (index_dir / "embeddings.npy").is_file() and (index_dir / "chunks.jsonl").is_file()


_index_cache: dict[str, tuple[tuple[int, int], list[dict], np.ndarray]] = {}


def _load_index(index_dir: Path) -> tuple[list[dict], np.ndarray]:
    stat = (index_dir / "embeddings.npy").stat()
    key = str(index_dir.resolve())
    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _index_cache.get(key)
    if cached and cached[0] == stamp:
        return cached[1], cached[2]
    records = [
        json.loads(line) for line in (index_dir / "chunks.jsonl").read_text().splitlines()
    ]
    embeddings = np.load(index_dir / "embeddings.npy").astype(np.float32)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / np.maximum(norms, 1e-12)
    _index_cache[key] = (stamp, records, embeddings)
    return records, embeddings


def retrieve(query: str, k: int, index_dir: Path, embedder: Embedder) -> list[Chunk]:
    try:
        records, embeddings = _load_index(index_dir)
    except FileNotFoundError:
        logger.info(f"no RAG index at {index_dir} — run ./locat.sh index-rag")
        return []
    if not records:
        return []
    query_vector = embedder.embed([query])[0]
    query_vector = query_vector / max(float(np.linalg.norm(query_vector)), 1e-12)
    scores = embeddings @ query_vector
    return [
        Chunk(
            text=records[i]["text"],
            source_path=records[i]["source_path"],
            page=records[i]["page"],
            score=float(scores[i]),
        )
        for i in np.argsort(scores)[::-1][:k]
    ]


def preflight_embed_model(model: str, api_url: str) -> None:
    """Fail fast with the exact remedy if Ollama is down or the embed model
    isn't pulled — mirrors bot._preflight_llm."""
    try:
        response = httpx.get(f"{api_url}/api/tags", timeout=5)
        response.raise_for_status()
        available = {str(m.get("name", "")) for m in response.json().get("models", [])}
    except (httpx.HTTPError, OSError) as exc:
        sys.exit(
            f"\n✖ Cannot reach Ollama at {api_url}\n"
            f"  Start it first:  ./locat.sh start   (or: ollama serve)\n"
            f"  Details: {exc}\n"
        )
    if not any(model == m or model.split(":")[0] == m.split(":")[0] for m in available):
        sys.exit(
            f"\n✖ Embedding model '{model}' is not available in Ollama at {api_url}\n"
            f"  Pull it:  ollama pull {model}\n"
        )


def _cli_index() -> None:
    import config

    data_dir = Path(config.rag_data_dir())
    index_dir = Path(config.rag_index_dir())
    data_dir.mkdir(parents=True, exist_ok=True)
    if not any(p.is_file() for p in data_dir.rglob("*")):
        print(
            f"no documents yet — put .txt/.md/.pdf files in {data_dir} "
            "and rerun ./locat.sh index-rag"
        )
        return
    preflight_embed_model(config.embed_model(), config.ollama_api_url())
    stats = index(
        data_dir,
        index_dir,
        OllamaEmbedder(config.ollama_api_url(), config.embed_model()),
        chunk_tokens=config.rag_chunk_tokens(),
        overlap=config.rag_chunk_overlap(),
    )
    print(
        f"indexed {stats.chunks} chunks from {stats.files} files "
        f"with {stats.embed_model} → {index_dir}"
    )


def stats_summary(index_dir: Path, data_dir: str) -> str:
    manifest_path = index_dir / "manifest.json"
    if not manifest_path.is_file():
        return "no index (run ./locat.sh index-rag)"
    manifest = json.loads(manifest_path.read_text())
    chunks = len((index_dir / "chunks.jsonl").read_text().splitlines())
    return f"index: {chunks} chunks from {len(manifest.get('files', {}))} files ({data_dir})"


def _cli_stats(bare: bool) -> None:
    import config

    index_dir = Path(config.rag_index_dir())
    print(stats_summary(index_dir, config.rag_data_dir()))
    manifest_path = index_dir / "manifest.json"
    if bare or not manifest_path.is_file():
        return
    manifest = json.loads(manifest_path.read_text())
    print(f"index dir:   {index_dir}")
    print(f"embed model: {manifest.get('embed_model')}")
    print(f"chunking:    {manifest.get('chunk_tokens')} tokens, {manifest.get('overlap')} overlap")
    for path in sorted(manifest.get("files", {})):
        print(f"  {path}")


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="offline document index for locat")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("index", help="rebuild the index from LOCAT_RAG_DATA_DIR")
    stats_parser = subcommands.add_parser("stats", help="print index summary")
    stats_parser.add_argument(
        "--bare", action="store_true", help="one status line, for ./locat.sh status"
    )
    args = parser.parse_args()
    if args.command == "index":
        _cli_index()
    else:
        _cli_stats(args.bare)


if __name__ == "__main__":
    _main()
