import csv
import shutil
from pathlib import Path

import pytest

import ingest
import retrieval
import store

REPO_ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
TRANSACTIONS_CSV = FIXTURES / "transactions-small.csv"
DATA_DIR = REPO_ROOT / "data" / "private"


@pytest.fixture
def db(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shutil.copy(TRANSACTIONS_CSV, data_dir / "transactions.csv")
    shutil.copy(FIXTURES / "note.txt", data_dir / "note.txt")
    shutil.copy(FIXTURES / "note.md", data_dir / "note.md")
    shutil.copy(DATA_DIR / "passphrase-smoke.txt", data_dir / "passphrase-smoke.txt")
    db_path = tmp_path / "lofi.db"
    ingest.ingest(data_dir, db_path, ingest.FakeEmbedder())
    return db_path


def fixture_rows() -> list[dict]:
    with TRANSACTIONS_CSV.open() as handle:
        return list(csv.DictReader(handle))


# ---- lookup ----


def test_lookup_by_transaction_id(db):
    gold = fixture_rows()[0]
    result = retrieval.lookup(db, gold["Id"])
    assert len(result["rows"]) == 1
    assert result["rows"][0]["id"] == int(gold["Id"])
    assert result["rows"][0]["merchant"] == gold["Merchant"]
    assert "elapsed_ms" in result


def test_lookup_by_merchant_token(db):
    result = retrieval.lookup(db, "Whole Foods")
    assert result["rows"]
    assert all("Whole Foods" in r["merchant"] for r in result["rows"])
    assert "elapsed_ms" in result


def test_lookup_limit_clamps_to_five(db):
    result = retrieval.lookup(db, "Co-op", limit=50)
    assert len(result["rows"]) <= 5


@pytest.mark.parametrize("injection", ['" OR 1', "NEAR(", '"""', "OR 1=1"])
def test_lookup_fts_injection_is_safe(db, injection):
    result = retrieval.lookup(db, injection)
    assert isinstance(result["rows"], list)
    assert "elapsed_ms" in result


# ---- filter_rows ----

OPERATOR_CASES = [
    ("category", "eq", "Groceries", None, lambda r: r["Category"] == "Groceries"),
    ("category", "neq", "Transfer", None, lambda r: r["Category"] != "Transfer"),
    ("merchant", "contains", "Fed", None, lambda r: "fed" in r["Merchant"].lower()),
    ("amount", "lt", "-100", None, lambda r: float(r["Amount"]) < -100),
    ("amount", "lte", "-412", None, lambda r: float(r["Amount"]) <= -412),
    ("amount", "gt", "0", None, lambda r: float(r["Amount"]) > 0),
    ("amount", "gte", "2400", None, lambda r: float(r["Amount"]) >= 2400),
    ("amount", "between", "-500", "-100", lambda r: -500 <= float(r["Amount"]) <= -100),
]


@pytest.mark.parametrize("column,operator,value,value2,predicate", OPERATOR_CASES)
def test_filter_rows_operator_matches_python_filter(db, column, operator, value, value2, predicate):
    gold_ids = [int(r["Id"]) for r in fixture_rows() if predicate(r)]
    gold_ids.sort()  # fixture ids are assigned in chronological order

    result = retrieval.filter_rows(
        db, column=column, operator=operator, value=value, value2=value2,
        order_by="date", descending=False, limit=5,
    )

    got_ids = [r["id"] for r in result["rows"]]
    assert got_ids == gold_ids[: len(got_ids)]
    assert len(result["rows"]) == min(5, len(gold_ids))
    assert "elapsed_ms" in result


def test_filter_rows_date_eq_year_month_prefix(db):
    result = retrieval.filter_rows(db, column="date", operator="eq", value="2025-03", limit=5)
    assert result["rows"]
    assert all(r["date"].startswith("2025-03") for r in result["rows"])


def test_filter_rows_category_eq_is_case_insensitive(db):
    result = retrieval.filter_rows(db, column="category", operator="eq", value="groceries", limit=5)
    assert result["rows"]
    assert all(r["category"] == "Groceries" for r in result["rows"])


def test_filter_rows_order_by_amount_abs(db):
    result = retrieval.filter_rows(
        db, column="category", operator="eq", value="Transfer",
        order_by="amount_abs", descending=True, limit=5,
    )
    amounts = [r["amount"] for r in result["rows"]]
    assert amounts == sorted(amounts, key=abs, reverse=True)


def test_filter_rows_unknown_category_returns_hint(db):
    result = retrieval.filter_rows(db, column="category", operator="eq", value="Grocery")
    assert result["rows"] == []
    assert "hint" in result
    assert "Groceries" in result["hint"]
    assert "elapsed_ms" in result


# ---- aggregate ----


def test_aggregate_sum_matches_python_sum_and_excludes_transfer_by_default(db):
    groceries = [r for r in fixture_rows() if r["Category"] == "Groceries"]
    gold_spent = round(sum(-float(r["Amount"]) for r in groceries if float(r["Amount"]) < 0), 2)
    gold_received = round(sum(float(r["Amount"]) for r in groceries if float(r["Amount"]) > 0), 2)

    result = retrieval.aggregate(
        db, operation="sum",
        filters=[{"column": "category", "operator": "eq", "value": "Groceries"}],
    )

    assert result["spent"] == pytest.approx(gold_spent)
    assert result["received"] == pytest.approx(gold_received)
    assert result["count"] == len(groceries)
    assert result["transfers_excluded"] is True
    assert "elapsed_ms" in result


def test_aggregate_include_transfers_toggle(db):
    excluded = retrieval.aggregate(db, operation="sum")
    included = retrieval.aggregate(db, operation="sum", include_transfers=True)

    assert excluded["transfers_excluded"] is True
    assert included["transfers_excluded"] is False
    assert included["spent"] > excluded["spent"]


# ---- semantic_search ----


def test_semantic_search_returns_exact_text_chunk_first(db):
    conn = store.connect(db)
    target_id, target_text = conn.execute(
        "select id, text from chunks where chunk_type = 'csv_row' limit 1"
    ).fetchone()
    conn.close()

    result = retrieval.semantic_search(db, target_text, ingest.FakeEmbedder(), k=3)

    assert result["results"][0]["text"] == target_text
    assert "elapsed_ms" in result


def test_semantic_search_finds_passphrase_canary(db):
    passphrase_text = " ".join((DATA_DIR / "passphrase-smoke.txt").read_text().split())

    result = retrieval.semantic_search(db, passphrase_text, ingest.FakeEmbedder(), k=3)

    assert any("BLUE MANATEE 436921" in r["text"] for r in result["results"])


def test_semantic_search_clamps_k_to_five(db):
    result = retrieval.semantic_search(db, "quokka capybara records", ingest.FakeEmbedder(), k=50)
    assert len(result["results"]) <= 5


def test_between_expands_month_prefixes(db):
    result = retrieval.filter_rows(
        db, column="date", operator="between", value="2025-03", value2="2025-03", limit=5
    )
    assert result["rows"]
    assert all(r["date"].startswith("2025-03") for r in result["rows"])


def test_between_expands_year_prefixes(db):
    result = retrieval.filter_rows(
        db, column="date", operator="between", value="2025", value2="2025", limit=5
    )
    assert result["rows"]


def test_between_mixed_prefix_and_full_date(db):
    result = retrieval.filter_rows(
        db, column="date", operator="between", value="2025-02-01", value2="2025-03", limit=5
    )
    assert result["rows"]
    assert all("2025-02-01" <= r["date"] <= "2025-03-31" for r in result["rows"])


def test_lte_date_prefix_includes_the_whole_period(db):
    result = retrieval.filter_rows(
        db, column="date", operator="lte", value="2025-02", order_by="date", descending=True, limit=5
    )
    assert result["rows"]
    assert result["rows"][0]["date"].startswith("2025-02")


def test_filter_rows_extra_filters_narrow_the_match(db):
    # Merchant condition + date-range condition in `filters` — the shape a
    # "merchant X in month Y" question needs (single-condition calls made the
    # analyst invent unsupported arg shapes and loop).
    unfiltered = retrieval.filter_rows(
        db, column="merchant", operator="eq", value="Whole Foods Co-op", limit=5
    )
    result = retrieval.filter_rows(
        db,
        column="merchant",
        operator="eq",
        value="Whole Foods Co-op",
        filters=[{"column": "date", "operator": "between", "value": "2025-03", "value2": "2025-03"}],
        limit=5,
    )
    assert result["rows"]
    assert len(result["rows"]) < len(unfiltered["rows"])
    assert all(r["merchant"] == "Whole Foods Co-op" for r in result["rows"])
    assert all(r["date"].startswith("2025-03") for r in result["rows"])
