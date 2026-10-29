"""SQLite storage layer for the RAG index: schema, connection, index-presence.

One file at config.rag_db_path(): typed transaction rows, FTS5 bm25 over
chunk text, and a lazily-created sqlite-vec vec0 table for embeddings (its
dimension isn't known until the first embed batch runs).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import httpx
import sqlite_vec

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, indexed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rows (
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL, merchant TEXT NOT NULL, category TEXT NOT NULL,
  account TEXT NOT NULL, statement TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '', amount_cents INTEGER NOT NULL,
  tags TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT '',
  reviewed TEXT NOT NULL DEFAULT '',
  source_path TEXT NOT NULL REFERENCES files(path));
CREATE INDEX IF NOT EXISTS idx_rows_date ON rows(date);
CREATE INDEX IF NOT EXISTS idx_rows_merchant ON rows(merchant COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_rows_category ON rows(category COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL, page INTEGER,
  chunk_type TEXT NOT NULL CHECK (chunk_type IN ('doc','csv_row','rollup')),
  row_id INTEGER, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open db_path, load sqlite-vec, and ensure the core schema exists."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    if not hasattr(conn, "enable_load_extension"):
        sys.exit(
            "\n✖ This Python's sqlite3 module cannot load extensions "
            "(needed for sqlite-vec).\n"
            "  Use a Python build with loadable-extension support (e.g. the "
            "python.org installer or Homebrew's python) and retry.\n"
        )
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the core tables/indexes if missing. Idempotent. Does not create
    chunks_vec — its embedding dimension isn't known until ingest's first
    embed batch."""
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def has_index(db_path: Path) -> bool:
    db_path = Path(db_path)
    if not db_path.is_file():
        return False
    conn = sqlite3.connect(str(db_path))
    try:
        exists = conn.execute(
            "select count(*) from sqlite_master where type='table' and name='chunks'"
        ).fetchone()[0]
        if not exists:
            return False
        return conn.execute("select count(*) from chunks").fetchone()[0] > 0
    finally:
        conn.close()


def date_range(db_path: Path) -> tuple[str, str] | None:
    """Earliest and latest transaction dates, or None when no rows are indexed."""
    db_path = Path(db_path)
    if not db_path.is_file():
        return None
    conn = sqlite3.connect(str(db_path))
    try:
        exists = conn.execute(
            "select count(*) from sqlite_master where type='table' and name='rows'"
        ).fetchone()[0]
        if not exists:
            return None
        first, last = conn.execute("select min(date), max(date) from rows").fetchone()
    finally:
        conn.close()
    return (first, last) if first else None


def categories(db_path: Path) -> list[str]:
    """Distinct transaction categories, sorted; empty when no rows are indexed."""
    db_path = Path(db_path)
    if not db_path.is_file():
        return []
    conn = sqlite3.connect(str(db_path))
    try:
        exists = conn.execute(
            "select count(*) from sqlite_master where type='table' and name='rows'"
        ).fetchone()[0]
        if not exists:
            return []
        rows = conn.execute("select distinct category from rows order by category").fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def stats_summary(db_path: Path, data_dir: str) -> str:
    db_path = Path(db_path)
    if not has_index(db_path):
        return "no index (run ./lofi.sh index-rag)"
    conn = sqlite3.connect(str(db_path))
    try:
        chunks = conn.execute("select count(*) from chunks").fetchone()[0]
        files = conn.execute("select count(*) from files").fetchone()[0]
    finally:
        conn.close()
    return f"index: {chunks} chunks from {files} files ({data_dir})"


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
            f"  Start it first:  ./lofi.sh start   (or: ollama serve)\n"
            f"  Details: {exc}\n"
        )
    if not any(model == m or model.split(":")[0] == m.split(":")[0] for m in available):
        sys.exit(
            f"\n✖ Embedding model '{model}' is not available in Ollama at {api_url}\n"
            f"  Pull it:  ollama pull {model}\n"
        )


def preflight(db_path: Path, embed_model: str, api_url: str) -> None:
    """With an index present the records agent embeds every semantic search, so
    the embed model must be pulled. No index → nothing to check."""
    if has_index(db_path):
        preflight_embed_model(embed_model, api_url)
