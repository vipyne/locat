"""Ingest documents + the transactions CSV into the SQLite RAG store.

Bot code only calls retrieval.py's functions against the db this builds. The
old chunks.jsonl/embeddings.npy/manifest.json files are now inert — never
deleted, just no longer read.
"""

import csv
import hashlib
import html
import os
import re
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol

import httpx
import numpy as np
import sqlite_vec
from loguru import logger
from pypdf import PdfReader

import store

SERIALIZER_VERSION = 1
SCHEMA_VERSION = 1
EMBED_BATCH = 64


@dataclass
class PageText:
    text: str
    source_path: str
    page: int | None


def extract(path: Path):
    """Doc files (.txt/.md/.pdf) -> list[PageText]. .csv -> list[dict] of
    typed rows (see _extract_csv). Unsupported extensions -> []."""
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


def _unescape(value: str) -> str:
    return html.unescape(value.strip())


def _extract_csv(path: Path, source_path: str) -> list[dict]:
    """Each row becomes a typed dict (rows-table columns) plus a `sentence`
    (serialize_row's prose) for the csv_row chunk."""
    with path.open(newline="") as handle:
        records = list(csv.DictReader(handle))
    rows = []
    for record in records:
        row = {
            "id": int(record["Id"]),
            "date": record["Date"].strip(),
            "merchant": _unescape(record["Merchant"]),
            "category": _unescape(record["Category"]),
            "account": _unescape(record["Account"]),
            "statement": _unescape(record.get("Original Statement", "")),
            "notes": _unescape(record.get("Notes", "")),
            "amount_cents": parse_amount_cents(record["Amount"]),
            "tags": _unescape(record.get("Tags", "")),
            "owner": _unescape(record.get("Owner", "")),
            "reviewed": _unescape(record.get("Reviewed", "")),
            "source_path": source_path,
        }
        row["sentence"] = serialize_row(row)
        rows.append(row)
    return rows


def parse_amount_cents(value: str) -> int:
    cleaned = value.strip().replace("$", "").replace(",", "")
    try:
        return int((Decimal(cleaned) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation as exc:
        raise ValueError(f"not a money amount: {value!r}") from exc


def _format_money(cents: int) -> str:
    cents = abs(cents)
    dollars, remainder = divmod(cents, 100)
    return f"${dollars:,}.{remainder:02d}"


def _format_date(date_str: str) -> str:
    dt = date_cls.fromisoformat(date_str)
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


def serialize_row(row: dict) -> str:
    """SERIALIZER_VERSION 1: one prose sentence-pair per row."""
    amount = _format_money(row["amount_cents"])
    if row["amount_cents"] < 0:
        action = (
            f"spent {amount} at {row['merchant']} ({row['category']}) "
            f"from account {row['account']}"
        )
    else:
        action = (
            f"received {amount} from {row['merchant']} ({row['category']}) "
            f"into account {row['account']}"
        )
    return (
        f"On {_format_date(row['date'])}, {action}. "
        f"Statement: {row['statement']}. "
        f"Transaction id {row['id']}."
    )


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


class OpenAIEmbedder:
    """Embeds via the OpenAI embeddings API: POST /v1/embeddings.

    Online mode only — the texts it sends leave the machine, so it is pointed
    at ``config.public_rag_data_dir()`` / ``config.public_rag_db_path()``.
    """

    def __init__(self, model: str, api_key: str, client: httpx.Client | None = None):
        self._url = "https://api.openai.com/v1/embeddings"
        self.model = model
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._client = client or httpx.Client(timeout=120.0)

    def embed(self, texts: list[str]) -> np.ndarray:
        response = self._client.post(
            self._url,
            headers=self._headers,
            json={"model": self.model, "input": texts},
        )
        response.raise_for_status()
        rows = [item["embedding"] for item in response.json()["data"]]
        return np.asarray(rows, dtype=np.float32)


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


@dataclass
class IngestStats:
    files: int
    chunks: int
    rows: int
    embedded: int
    embed_model: str
    rebuilt: bool
    reason: str | None = None


def _meta_get(conn, key: str) -> str | None:
    row = conn.execute("select value from meta where key = ?", (key,)).fetchone()
    return row[0] if row else None


def _meta_set(conn, key: str, value: str) -> None:
    conn.execute(
        "insert into meta (key, value) values (?, ?) "
        "on conflict(key) do update set value = excluded.value",
        (key, value),
    )


def _vec_table_exists(conn) -> bool:
    return (
        conn.execute(
            "select count(*) from sqlite_master where name = 'chunks_vec'"
        ).fetchone()[0]
        > 0
    )


def _ensure_vec_table(conn, dim: int) -> None:
    if not _vec_table_exists(conn):
        conn.execute(f"CREATE VIRTUAL TABLE chunks_vec USING vec0(embedding float[{dim}])")
        _meta_set(conn, "embed_dim", str(dim))


def _insert_chunks(conn, embedder, entries: list[dict]) -> int:
    """entries: [{source, page, chunk_type, row_id, text}, ...]. Embeds and
    inserts into chunks + chunks_fts + chunks_vec, keyed by chunks.id. Returns
    the number of texts embedded."""
    if not entries:
        return 0
    texts = [e["text"] for e in entries]
    batches = [
        embedder.embed(texts[i : i + EMBED_BATCH]) for i in range(0, len(texts), EMBED_BATCH)
    ]
    vectors = np.vstack(batches)
    _ensure_vec_table(conn, vectors.shape[1])
    for entry, vector in zip(entries, vectors):
        cursor = conn.execute(
            "insert into chunks (source, page, chunk_type, row_id, text) values (?, ?, ?, ?, ?)",
            (entry["source"], entry["page"], entry["chunk_type"], entry.get("row_id"), entry["text"]),
        )
        chunk_id = cursor.lastrowid
        conn.execute("insert into chunks_fts(rowid, text) values (?, ?)", (chunk_id, entry["text"]))
        conn.execute(
            "insert into chunks_vec(rowid, embedding) values (?, ?)",
            (chunk_id, sqlite_vec.serialize_float32(vector.tolist())),
        )
    return len(entries)


def _purge_file(conn, path: str) -> None:
    chunk_ids = [
        r[0] for r in conn.execute("select id from chunks where source = ?", (path,)).fetchall()
    ]
    vec_exists = _vec_table_exists(conn)
    for chunk_id in chunk_ids:
        conn.execute("delete from chunks_fts where rowid = ?", (chunk_id,))
        if vec_exists:
            conn.execute("delete from chunks_vec where rowid = ?", (chunk_id,))
    conn.execute("delete from chunks where source = ?", (path,))
    conn.execute("delete from rows where source_path = ?", (path,))
    conn.execute("delete from files where path = ?", (path,))


def _full_wipe(conn) -> None:
    conn.execute("delete from chunks_fts")
    if _vec_table_exists(conn):
        conn.execute("drop table chunks_vec")
    conn.execute("delete from chunks")
    conn.execute("delete from rows")
    conn.execute("delete from files")


def _ingest_file(
    conn, path: Path, resolved: str, sha256: str, embedder, chunk_tokens: int, overlap: int
) -> int:
    embedded = 0
    if path.suffix.lower() == ".csv":
        entries = []
        for row in extract(path):
            conn.execute(
                """insert into rows
                   (id, date, merchant, category, account, statement, notes,
                    amount_cents, tags, owner, reviewed, source_path)
                   values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["id"], row["date"], row["merchant"], row["category"],
                    row["account"], row["statement"], row["notes"], row["amount_cents"],
                    row["tags"], row["owner"], row["reviewed"], resolved,
                ),
            )
            entries.append(
                {
                    "source": resolved, "page": None, "chunk_type": "csv_row",
                    "row_id": row["id"], "text": row["sentence"],
                }
            )
        embedded += _insert_chunks(conn, embedder, entries)
    else:
        entries = [
            {"source": resolved, "page": page.page, "chunk_type": "doc", "row_id": None, "text": text}
            for page in extract(path)
            for text in chunk_text(page.text, chunk_tokens, overlap)
        ]
        embedded += _insert_chunks(conn, embedder, entries)
    conn.execute(
        "insert into files (path, sha256, indexed_at) values (?, ?, ?)",
        (resolved, sha256, datetime.now(timezone.utc).isoformat()),
    )
    return embedded


def _rebuild_rollups(conn, embedder) -> int:
    """Monthly x active-category rollups over `rows`, Transfer excluded.
    Regenerated in full whenever the CSV changes."""
    old_ids = [
        r[0] for r in conn.execute("select id from chunks where chunk_type = 'rollup'").fetchall()
    ]
    vec_exists = _vec_table_exists(conn)
    for chunk_id in old_ids:
        conn.execute("delete from chunks_fts where rowid = ?", (chunk_id,))
        if vec_exists:
            conn.execute("delete from chunks_vec where rowid = ?", (chunk_id,))
    conn.execute("delete from chunks where chunk_type = 'rollup'")

    groups = conn.execute(
        """select substr(date, 1, 7) as ym, category, count(*), sum(-amount_cents)
           from rows
           where category != 'Transfer' and amount_cents < 0
           group by ym, category"""
    ).fetchall()

    entries = []
    for ym, category, count, total_cents in groups:
        year, month = ym.split("-")
        month_name = date_cls(int(year), int(month), 1).strftime("%B")
        largest_merchant, largest_date, largest_cents = conn.execute(
            """select merchant, date, amount_cents from rows
               where category = ? and substr(date, 1, 7) = ? and amount_cents < 0
               order by amount_cents asc limit 1""",
            (category, ym),
        ).fetchone()
        largest_dt = date_cls.fromisoformat(largest_date)
        text = (
            f"In {month_name} {year}, {count} transactions in {category} totaled "
            f"{_format_money(total_cents)} spent; largest: {_format_money(largest_cents)} "
            f"at {largest_merchant} on {largest_dt.strftime('%B')} {largest_dt.day}."
        )
        entries.append({"source": "rollup", "page": None, "chunk_type": "rollup", "row_id": None, "text": text})

    return _insert_chunks(conn, embedder, entries)


def ingest(
    data_dir: Path,
    db_path: Path,
    embedder,
    chunk_tokens: int = 500,
    overlap: int = 50,
) -> IngestStats:
    data_dir = Path(data_dir)
    conn = store.connect(db_path)
    try:
        prior_embed_model = _meta_get(conn, "embed_model")
        prior_serializer = _meta_get(conn, "serializer_version")
        prior_schema = _meta_get(conn, "schema_version")
        prior_chunk_tokens = _meta_get(conn, "chunk_tokens")
        prior_chunk_overlap = _meta_get(conn, "chunk_overlap")

        rebuilt = False
        reason = None
        if prior_embed_model is not None:
            if prior_embed_model != embedder.model:
                reason = f"embed_model changed ({prior_embed_model} -> {embedder.model})"
            elif prior_serializer != str(SERIALIZER_VERSION):
                reason = f"serializer_version changed ({prior_serializer} -> {SERIALIZER_VERSION})"
            elif prior_schema != str(SCHEMA_VERSION):
                reason = f"schema_version changed ({prior_schema} -> {SCHEMA_VERSION})"
            elif prior_chunk_tokens not in (None, str(chunk_tokens)):
                reason = f"chunk_tokens changed ({prior_chunk_tokens} -> {chunk_tokens})"
            elif prior_chunk_overlap not in (None, str(overlap)):
                reason = f"chunk_overlap changed ({prior_chunk_overlap} -> {overlap})"
            if reason:
                logger.info(f"full rebuild: {reason}")
                _full_wipe(conn)
                rebuilt = True

        _meta_set(conn, "embed_model", embedder.model)
        _meta_set(conn, "serializer_version", str(SERIALIZER_VERSION))
        _meta_set(conn, "schema_version", str(SCHEMA_VERSION))
        _meta_set(conn, "chunk_tokens", str(chunk_tokens))
        _meta_set(conn, "chunk_overlap", str(overlap))

        disk_files = sorted(p for p in data_dir.rglob("*") if p.is_file())
        disk_paths = {str(p.resolve()) for p in disk_files}
        known = dict(conn.execute("select path, sha256 from files").fetchall())

        for stale_path in set(known) - disk_paths:
            _purge_file(conn, stale_path)

        embedded = 0
        csv_changed = False
        for path in disk_files:
            resolved = str(path.resolve())
            sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
            if known.get(resolved) == sha256:
                continue
            if resolved in known:
                _purge_file(conn, resolved)
            embedded += _ingest_file(conn, path, resolved, sha256, embedder, chunk_tokens, overlap)
            if path.suffix.lower() == ".csv":
                csv_changed = True

        if csv_changed:
            embedded += _rebuild_rollups(conn, embedder)

        conn.commit()

        return IngestStats(
            files=conn.execute("select count(*) from files").fetchone()[0],
            chunks=conn.execute("select count(*) from chunks").fetchone()[0],
            rows=conn.execute("select count(*) from rows").fetchone()[0],
            embedded=embedded,
            embed_model=embedder.model,
            rebuilt=rebuilt,
            reason=reason,
        )
    finally:
        conn.close()


def _build_embedder(name: str):
    """``ollama`` (private index, local) or ``openai`` (public index, cloud)."""
    import config

    if name == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise SystemExit(
                "\n✖ the public index needs OPENAI_API_KEY — put it in .env "
                "and rerun ./lofi.sh index-rag\n"
            )
        return OpenAIEmbedder(config.online_embed_model(), api_key)
    store.preflight_embed_model(config.embed_model(), config.ollama_api_url())
    return OllamaEmbedder(config.ollama_api_url(), config.embed_model())


def _cli_index(data_dir=None, db=None, embedder: str = "ollama") -> None:
    import config

    public = embedder == "openai"
    if data_dir is None:
        data_dir = config.public_rag_data_dir() if public else config.rag_data_dir()
    if db is None:
        db = config.public_rag_db_path() if public else config.rag_db_path()
    data_dir = Path(data_dir)
    db_path = Path(db)
    if public and db_path.resolve() == Path(config.rag_db_path()).resolve():
        raise SystemExit(
            f"\n✖ refusing to write cloud embeddings into the private index "
            f"({db_path}) — pass --db with the public index path\n"
        )

    data_dir.mkdir(parents=True, exist_ok=True)
    if not any(p.is_file() for p in data_dir.rglob("*")):
        print(
            f"no documents yet — put .txt/.md/.csv/.pdf files in {data_dir} "
            "and rerun ./lofi.sh index-rag"
        )
        return
    built = _build_embedder(embedder)
    try:
        stats = ingest(
            data_dir,
            db_path,
            built,
            chunk_tokens=config.rag_chunk_tokens(),
            overlap=config.rag_chunk_overlap(),
        )
    except SystemExit:
        raise
    except Exception as exc:
        label = "public" if public else "offline"
        raise SystemExit(f"\n✖ {label} index build failed: {exc}\n") from exc
    hint = ""
    if not public:
        old_index_dir = Path(config.rag_index_dir())
        if (old_index_dir / "chunks.jsonl").is_file() or (
            old_index_dir / "embeddings.npy"
        ).is_file():
            hint = (
                f" (old chunks.jsonl/embeddings.npy/manifest.json under "
                f"{old_index_dir} are now inert)"
            )
    reason = f" [{stats.reason}]" if stats.reason else ""
    print(
        f"indexed {stats.chunks} chunks from {stats.files} files, {stats.rows} rows "
        f"with {stats.embed_model} → {db_path}{reason}{hint}"
    )


def _cli_stats(bare: bool, public: bool = False) -> None:
    import config

    if public:
        db_path = Path(config.public_rag_db_path())
        data_dir = config.public_rag_data_dir()
    else:
        db_path = Path(config.rag_db_path())
        data_dir = config.rag_data_dir()
    print(store.stats_summary(db_path, data_dir))
    if bare or not store.has_index(db_path):
        return
    conn = store.connect(db_path)
    embed_model = _meta_get(conn, "embed_model")
    chunk_tokens = _meta_get(conn, "chunk_tokens")
    chunk_overlap = _meta_get(conn, "chunk_overlap")
    files = [r[0] for r in conn.execute("select path from files order by path").fetchall()]
    conn.close()
    print(f"db path:     {db_path}")
    print(f"embed model: {embed_model}")
    print(f"chunking:    {chunk_tokens} tokens, {chunk_overlap} overlap")
    for path in files:
        print(f"  {path}")


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="offline document + transactions index for lofi")
    subcommands = parser.add_subparsers(dest="command", required=True)
    index_parser = subcommands.add_parser("index", help="rebuild the index from LOFI_RAG_DATA_DIR")
    index_parser.add_argument("--data-dir", help="documents to index (default: per --embedder)")
    index_parser.add_argument("--db", help="SQLite index to write (default: per --embedder)")
    index_parser.add_argument(
        "--embedder",
        choices=("ollama", "openai"),
        default="ollama",
        help="ollama = private index (local); openai = public index (cloud)",
    )
    stats_parser = subcommands.add_parser("stats", help="print index summary")
    stats_parser.add_argument(
        "--bare", action="store_true", help="one status line, for ./lofi.sh status"
    )
    stats_parser.add_argument(
        "--public", action="store_true", help="report the public (online-mode) index"
    )
    args = parser.parse_args()
    if args.command == "index":
        _cli_index(data_dir=args.data_dir, db=args.db, embedder=args.embedder)
    else:
        _cli_stats(args.bare, args.public)


if __name__ == "__main__":
    _main()
