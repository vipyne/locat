from pathlib import Path

import store

EXPECTED_TABLES = {"meta", "files", "rows", "chunks", "chunks_fts"}


def _table_names(conn) -> set[str]:
    rows = conn.execute(
        "select name from sqlite_master where type in ('table','view')"
    ).fetchall()
    return {r[0] for r in rows}


def _insert_one_file_and_chunk(conn) -> None:
    conn.execute(
        "insert into files (path, sha256, indexed_at) values (?, ?, ?)",
        ("/x.txt", "a" * 64, "2026-01-01T00:00:00"),
    )
    conn.execute(
        "insert into chunks (source, page, chunk_type, row_id, text) values (?, ?, ?, ?, ?)",
        ("/x.txt", None, "doc", None, "hello world"),
    )
    conn.commit()


def test_ensure_schema_creates_expected_tables(tmp_path):
    conn = store.connect(tmp_path / "lofi.db")
    assert EXPECTED_TABLES <= _table_names(conn)
    conn.close()


def test_ensure_schema_idempotent(tmp_path):
    db_path = tmp_path / "lofi.db"
    conn = store.connect(db_path)
    before = _table_names(conn)
    store.ensure_schema(conn)
    store.ensure_schema(conn)
    after = _table_names(conn)
    assert before == after
    conn.close()


def test_connect_loads_vec_extension(tmp_path):
    conn = store.connect(tmp_path / "lofi.db")
    version = conn.execute("select vec_version()").fetchone()[0]
    assert isinstance(version, str) and version
    conn.close()


def test_has_index_false_on_missing_db(tmp_path):
    assert store.has_index(tmp_path / "nope.db") is False


def test_has_index_false_on_empty_db(tmp_path):
    db_path = tmp_path / "lofi.db"
    store.connect(db_path).close()
    assert store.has_index(db_path) is False


def test_has_index_true_after_chunks_present(tmp_path):
    db_path = tmp_path / "lofi.db"
    conn = store.connect(db_path)
    _insert_one_file_and_chunk(conn)
    conn.close()
    assert store.has_index(db_path) is True


def test_stats_summary_no_index_missing_db(tmp_path):
    assert (
        store.stats_summary(tmp_path / "nope.db", "data")
        == "no index (run ./lofi.sh index-rag)"
    )


def test_stats_summary_no_index_empty_db(tmp_path):
    db_path = tmp_path / "lofi.db"
    store.connect(db_path).close()
    assert (
        store.stats_summary(db_path, "data")
        == "no index (run ./lofi.sh index-rag)"
    )


def test_stats_summary_populated(tmp_path):
    db_path = tmp_path / "lofi.db"
    conn = store.connect(db_path)
    _insert_one_file_and_chunk(conn)
    conn.execute(
        "insert into chunks (source, page, chunk_type, row_id, text) values (?, ?, ?, ?, ?)",
        ("/x.txt", None, "doc", None, "second chunk"),
    )
    conn.commit()
    conn.close()
    assert store.stats_summary(db_path, "data") == "index: 2 chunks from 1 files (data)"


def test_date_range_returns_min_and_max(tmp_path):
    db_path = tmp_path / "lofi.db"
    conn = store.connect(db_path)
    _insert_one_file_and_chunk(conn)
    conn.executemany(
        "insert into rows (id, date, merchant, category, account, amount_cents, source_path)"
        " values (?, ?, 'M', 'C', 'A', -100, '/x.txt')",
        [(1, "2025-03-01"), (2, "2024-01-18"), (3, "2026-09-08")],
    )
    conn.commit()
    conn.close()
    assert store.date_range(db_path) == ("2024-01-18", "2026-09-08")


def test_date_range_none_without_rows(tmp_path):
    db_path = tmp_path / "lofi.db"
    store.connect(db_path).close()
    assert store.date_range(db_path) is None


def test_date_range_none_for_missing_db(tmp_path):
    assert store.date_range(tmp_path / "missing.db") is None


def test_categories_sorted_distinct(tmp_path):
    db_path = tmp_path / "lofi.db"
    conn = store.connect(db_path)
    _insert_one_file_and_chunk(conn)
    conn.executemany(
        "insert into rows (id, date, merchant, category, account, amount_cents, source_path)"
        " values (?, '2025-01-01', 'M', ?, 'A', -100, '/x.txt')",
        [(1, "Groceries"), (2, "Charity"), (3, "Groceries")],
    )
    conn.commit()
    conn.close()
    assert store.categories(db_path) == ["Charity", "Groceries"]


def test_categories_empty_without_db(tmp_path):
    assert store.categories(tmp_path / "missing.db") == []
