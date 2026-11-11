"""Read-only retrieval functions over the RAG SQLite store: lookup,
filter_rows, aggregate, semantic_search. Pure functions, parameterized SQL
only — no model ever authors SQL. Shared by tools, evals, and tests.
"""

from __future__ import annotations

import difflib
import sqlite3
import time

import sqlite_vec

import store
from ingest import parse_amount_cents

COLUMN_SQL = {
    "date": "date",
    "merchant": "merchant",
    "category": "category",
    "account": "account",
    "amount": "amount_cents",
}
TEXT_COLUMNS = {"merchant", "category", "account"}
ORDER_SQL = {"date": "date", "amount": "amount_cents", "amount_abs": "abs(amount_cents)"}
OPERATORS = {"eq", "neq", "contains", "lt", "lte", "gt", "gte", "between"}

SELECT_ROW_SQL = (
    "select id, date, merchant, category, account, statement, notes, "
    "amount_cents, tags, owner, reviewed from rows"
)


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


def _row_to_dict(row: tuple) -> dict:
    (id_, date, merchant, category, account, statement, notes,
     amount_cents, tags, owner, reviewed) = row
    return {
        "id": id_, "date": date, "merchant": merchant, "category": category,
        "account": account, "statement": statement, "notes": notes,
        "amount": amount_cents / 100, "tags": tags, "owner": owner, "reviewed": reviewed,
    }


def _rows_by_ids(conn: sqlite3.Connection, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    fetched = {
        r[0]: _row_to_dict(r)
        for r in conn.execute(f"{SELECT_ROW_SQL} where id in ({placeholders})", ids)
    }
    return [fetched[i] for i in ids if i in fetched]


def _quote_fts_query(text: str) -> str:
    """Double-quote each token, escaping embedded quotes, so injection-shaped
    input (NEAR(, OR, bare quotes) is treated as literal search text."""
    tokens = text.split()
    if not tokens:
        return '""'
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def _date_period_end(value: str) -> str:
    # "2025" and "2025-03" widen to the period's last day; full dates pass through.
    value = str(value).strip()
    if len(value) == 4:
        return f"{value}-12-31"
    if len(value) == 7:
        return f"{value}-31"
    return value


def _date_period_start(value: str) -> str:
    value = str(value).strip()
    if len(value) == 4:
        return f"{value}-01-01"
    if len(value) == 7:
        return f"{value}-01"
    return value


def _filter_clause(column: str, operator: str, value, value2=None) -> tuple[str, list]:
    if column not in COLUMN_SQL:
        raise ValueError(f"unknown column: {column}")
    if operator not in OPERATORS:
        raise ValueError(f"unknown operator: {operator}")
    sql_col = COLUMN_SQL[column]
    is_text = column in TEXT_COLUMNS

    if column == "amount":
        value = parse_amount_cents(str(value))
        if value2 is not None:
            value2 = parse_amount_cents(str(value2))

    if column == "date" and operator == "eq":
        # prefix match: "2025", "2025-03", or a full "2025-03-12" all narrow correctly
        return f"{sql_col} LIKE ?", [f"{value}%"]
    if column == "date" and operator == "between":
        return f"{sql_col} BETWEEN ? AND ?", [_date_period_start(value), _date_period_end(value2)]
    if column == "date" and operator in ("lte", "gt"):
        value = _date_period_end(value)
    if operator == "eq":
        return f"{sql_col} = ?" + (" COLLATE NOCASE" if is_text else ""), [value]
    if operator == "neq":
        return f"{sql_col} != ?" + (" COLLATE NOCASE" if is_text else ""), [value]
    if operator == "contains":
        return f"{sql_col} LIKE ? COLLATE NOCASE", [f"%{value}%"]
    if operator == "between":
        return f"{sql_col} BETWEEN ? AND ?", [value, value2]
    sql_op = {"lt": "<", "lte": "<=", "gt": ">", "gte": ">="}[operator]
    return f"{sql_col} {sql_op} ?", [value]


def _category_hint(conn: sqlite3.Connection, value: str) -> list[str]:
    categories = [r[0] for r in conn.execute("select distinct category from rows").fetchall()]
    return difflib.get_close_matches(value, categories, n=3, cutoff=0.5)


def lookup(db_path, query: str, limit: int = 3) -> dict:
    start = time.perf_counter()
    limit = min(limit, 5)
    conn = store.connect(db_path)
    try:
        stripped = query.strip()
        if stripped.isdigit() and len(stripped) >= 10:
            row = conn.execute(f"{SELECT_ROW_SQL} where id = ?", (int(stripped),)).fetchone()
            rows = [_row_to_dict(row)] if row else []
            return {"rows": rows, "elapsed_ms": _elapsed_ms(start)}

        fts_query = _quote_fts_query(query)
        ranked = conn.execute(
            """select chunks.row_id, bm25(chunks_fts) as score
               from chunks_fts
               join chunks on chunks.id = chunks_fts.rowid
               where chunks_fts match ? and chunks.chunk_type = 'csv_row'
               order by score
               limit ?""",
            (fts_query, limit),
        ).fetchall()
        rows = _rows_by_ids(conn, [r[0] for r in ranked])
        return {"rows": rows, "elapsed_ms": _elapsed_ms(start)}
    finally:
        conn.close()


def filter_rows(
    db_path,
    column: str,
    operator: str,
    value,
    value2=None,
    filters: list[dict] | None = None,
    order_by: str = "date",
    descending: bool = True,
    limit: int = 5,
) -> dict:
    start = time.perf_counter()
    limit = min(limit, 5)
    conn = store.connect(db_path)
    try:
        clause, params = _filter_clause(column, operator, value, value2)
        extra_clauses: list[str] = []
        extra_params: list = []
        for f in filters or []:
            f_clause, f_params = _filter_clause(
                f["column"], f["operator"], f["value"], f.get("value2")
            )
            extra_clauses.append(f_clause)
            extra_params.extend(f_params)
        where_sql = " and ".join([clause, *extra_clauses])
        order_sql = ORDER_SQL.get(order_by, "date")
        direction = "desc" if descending else "asc"
        query = f"{SELECT_ROW_SQL} where {where_sql} order by {order_sql} {direction} limit ?"
        rows = [
            _row_to_dict(r)
            for r in conn.execute(query, [*params, *extra_params, limit]).fetchall()
        ]

        if not rows and column == "category" and operator == "eq":
            like_clause, like_params = _filter_clause("category", "contains", value)
            like_where = " and ".join([like_clause, *extra_clauses])
            like_query = f"{SELECT_ROW_SQL} where {like_where} order by {order_sql} {direction} limit ?"
            rows = [
                _row_to_dict(r)
                for r in conn.execute(like_query, [*like_params, *extra_params, limit]).fetchall()
            ]
            if not rows:
                hint = _category_hint(conn, value)
                elapsed_ms = _elapsed_ms(start)
                if hint:
                    return {"rows": [], "hint": f"closest: {hint}", "elapsed_ms": elapsed_ms}
                return {"rows": [], "elapsed_ms": elapsed_ms}

        return {"rows": rows, "elapsed_ms": _elapsed_ms(start)}
    finally:
        conn.close()


def aggregate(
    db_path,
    operation: str,
    column: str = "amount",
    filters: list[dict] | None = None,
    include_transfers: bool = False,
) -> dict:
    start = time.perf_counter()
    filters = filters or []
    conn = store.connect(db_path)
    try:
        clauses = ["1=1"]
        params: list = []
        if not include_transfers:
            clauses.append("category != 'Transfer'")
        for f in filters:
            clause, p = _filter_clause(f["column"], f["operator"], f["value"], f.get("value2"))
            clauses.append(clause)
            params.extend(p)
        where_sql = " and ".join(clauses)

        if operation == "sum" and column == "amount":
            spent_cents, received_cents, net_cents, count = conn.execute(
                f"""select
                    coalesce(sum(case when amount_cents < 0 then -amount_cents else 0 end), 0),
                    coalesce(sum(case when amount_cents > 0 then amount_cents else 0 end), 0),
                    coalesce(sum(amount_cents), 0),
                    count(*)
                    from rows where {where_sql}""",
                params,
            ).fetchone()
            return {
                "spent": spent_cents / 100,
                "received": received_cents / 100,
                "net": net_cents / 100,
                "count": count,
                "transfers_excluded": not include_transfers,
                "elapsed_ms": _elapsed_ms(start),
            }

        sql_col = COLUMN_SQL.get(column, "amount_cents")
        value, count = conn.execute(
            f"select {operation}({sql_col}), count(*) from rows where {where_sql}", params
        ).fetchone()
        if column == "amount" and value is not None and operation != "count":
            value = value / 100
        return {
            "result": value,
            "count": count,
            "transfers_excluded": not include_transfers,
            "elapsed_ms": _elapsed_ms(start),
        }
    finally:
        conn.close()


def rrf(*rankings: dict[int, int], k: int = 60) -> list[tuple[int, float]]:
    """Reciprocal rank fusion over any number of {doc_id: rank(1-based)} maps."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for doc_id, rank in ranking.items():
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def rerank(fused: list[tuple[int, float]]) -> list[tuple[int, float]]:
    """No-op hook — a future cross-encoder reranker slots in here."""
    return fused


def semantic_search(db_path, query: str, embedder, k: int = 4) -> dict:
    start = time.perf_counter()
    k = min(k, 5)
    conn = store.connect(db_path)
    try:
        vector = embedder.embed([query])[0]
        vec_rows = conn.execute(
            """select rowid, distance from chunks_vec
               where embedding match ? and k = 20
               order by distance""",
            (sqlite_vec.serialize_float32(vector.tolist()),),
        ).fetchall()
        vec_rank = {rowid: rank for rank, (rowid, _distance) in enumerate(vec_rows, start=1)}

        fts_query = _quote_fts_query(query)
        fts_rows = conn.execute(
            "select rowid, bm25(chunks_fts) as score from chunks_fts where chunks_fts match ? "
            "order by score limit 20",
            (fts_query,),
        ).fetchall()
        fts_rank = {rowid: rank for rank, (rowid, _score) in enumerate(fts_rows, start=1)}

        fused = rerank(rrf(vec_rank, fts_rank))
        results = []
        for chunk_id, _score in fused[:k]:
            row = conn.execute(
                "select source, page, chunk_type, row_id, text from chunks where id = ?",
                (chunk_id,),
            ).fetchone()
            if row:
                results.append(
                    {"source": row[0], "page": row[1], "chunk_type": row[2], "row_id": row[3], "text": row[4]}
                )
        return {"results": results, "elapsed_ms": _elapsed_ms(start)}
    finally:
        conn.close()
