"""Tests for tools.build_tools — the four retrieval FunctionSchemas the
pipeline model gets in the single-model cascade. Handlers run real retrieval
against a fixture DB; the contract per call: a plain-dict result through
result_callback (≤5 items, error dict on bad args) and one `lofi-rag` RTVI
frame carrying the route line.
"""

import asyncio
import shutil
from pathlib import Path

import pytest
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pipecat.services.llm_service import FunctionCallParams

import ingest
import tools

FIXTURES = Path(__file__).parent / "fixtures"
TOOL_NAMES = ["lookup", "filter_rows", "aggregate", "semantic_search"]


@pytest.fixture
def db(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shutil.copy(FIXTURES / "transactions-small.csv", data_dir / "transactions.csv")
    shutil.copy(FIXTURES / "note.txt", data_dir / "note.txt")
    db_path = tmp_path / "lofi.db"
    ingest.ingest(data_dir, db_path, ingest.FakeEmbedder())
    return db_path


class RecordingLLM:
    def __init__(self):
        self.frames = []

    async def push_frame(self, frame, *args, **kwargs):
        self.frames.append(frame)


def schemas_by_name(db_path):
    built = tools.build_tools(db_path, ingest.FakeEmbedder())
    return {s.name: s for s in built}


def call_tool(db_path, name, arguments):
    schema = schemas_by_name(db_path)[name]
    llm = RecordingLLM()
    delivered = []

    async def result_callback(value, *args, **kwargs):
        delivered.append(value)

    async def run():
        await schema.handler(
            FunctionCallParams(
                function_name=name,
                tool_call_id="call-1",
                arguments=arguments,
                llm=llm,
                pipeline_worker=None,
                context=None,
                result_callback=result_callback,
            )
        )

    asyncio.run(run())
    assert len(delivered) == 1
    return delivered[0], llm.frames


def rag_frames(frames):
    return [
        f
        for f in frames
        if isinstance(f, RTVIServerMessageFrame) and f.data.get("type") == tools.RAG_MESSAGE_TYPE
    ]


# ---- schemas ----


def test_build_tools_returns_the_four_schemas(db):
    schemas = schemas_by_name(db)
    assert sorted(schemas) == sorted(TOOL_NAMES)
    assert all(isinstance(s, FunctionSchema) and s.handler for s in schemas.values())


def test_params_is_the_single_source_of_truth(db):
    assert sorted(tools.PARAMS) == sorted(TOOL_NAMES)
    schemas = schemas_by_name(db)
    for name, spec in tools.PARAMS.items():
        assert schemas[name].properties == spec["properties"]
        assert schemas[name].required == spec["required"]
        assert schemas[name].description == spec["description"]


def test_filter_rows_schema_constrains_columns_and_operators(db):
    props = tools.PARAMS["filter_rows"]["properties"]
    assert sorted(props["column"]["enum"]) == ["account", "amount", "category", "date", "merchant"]
    assert sorted(props["operator"]["enum"]) == [
        "between", "contains", "eq", "gt", "gte", "lt", "lte", "neq",
    ]


# ---- handlers ----


def test_lookup_returns_rows_dict(db):
    result, frames = call_tool(db, "lookup", {"query": "Harborview"})
    assert isinstance(result, dict)
    assert result["rows"]
    assert len(result["rows"]) <= 5
    assert len(rag_frames(frames)) == 1


def test_aggregate_returns_totals(db):
    result, _ = call_tool(
        db,
        "aggregate",
        {"operation": "sum", "filters": [{"column": "category", "operator": "eq", "value": "Groceries"}]},
    )
    assert result["spent"] > 0
    assert result["transfers_excluded"] is True


def test_semantic_search_frame_carries_chunks(db):
    note_text = " ".join((FIXTURES / "note.txt").read_text().split())
    result, frames = call_tool(db, "semantic_search", {"query": note_text})
    assert result["results"]
    data = rag_frames(frames)[0].data
    assert data["chunks"]
    assert all("source" in c and "text" in c for c in data["chunks"])


def test_rag_frame_has_the_route_line_shape(db):
    _, frames = call_tool(db, "lookup", {"query": "Harborview"})
    data = rag_frames(frames)[0].data
    assert data["tool"] == "lookup"
    assert data["args"] == {"query": "Harborview"}
    assert isinstance(data["count"], int)
    assert isinstance(data["elapsed_ms"], (int, float))
    assert data["chunks"] == []


def test_bad_args_return_error_dict_not_raise(db):
    result, frames = call_tool(db, "filter_rows", {"column": "amount", "operator": "gt", "value": "a lot"})
    assert set(result) == {"error"}
    assert result["error"]
    assert rag_frames(frames) == []


def test_unknown_arg_returns_error_dict(db):
    result, _ = call_tool(db, "lookup", {"merchant_name": "Harborview"})
    assert "error" in result


def test_string_arguments_are_parsed(db):
    result, _ = call_tool(db, "lookup", '{"query": "Harborview"}')
    assert result["rows"]


def test_results_never_exceed_five(db):
    result, frames = call_tool(
        db, "filter_rows", {"column": "amount", "operator": "lt", "value": "0", "limit": 50}
    )
    assert len(result["rows"]) <= 5
    assert rag_frames(frames)[0].data["count"] <= 5
