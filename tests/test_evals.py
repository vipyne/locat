"""Offline tests for evals/run_evals.py stage logic — no network: the analyst
is monkeypatched; only query routing/scoring runs."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))

import run_evals  # noqa: E402

import rag_agent  # noqa: E402

HISTORY = [
    {"role": "user", "content": "what did I buy at Paulie's Delicatessen?"},
    {"role": "assistant", "content": "You spent $40.00 at Paulie's Delicatessen on August 24, 2026."},
]


class FakeV1Client:
    """Scripted /v1/chat/completions replies; sticky last entry; exceptions raise.

    A step is a content string, a full message dict (e.g. with tool_calls), or
    an exception."""

    def __init__(self, script):
        self._script = list(script)
        self.requests = []

    async def post(self, url, json=None, **kwargs):
        self.requests.append(json)
        step = self._script.pop(0) if len(self._script) > 1 else self._script[0]
        if isinstance(step, BaseException):
            raise step
        message = step if isinstance(step, dict) else {"content": step}

        class R:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": message}]}

        return R()


def run_front(queries, script):
    client = FakeV1Client(script)
    return asyncio.run(run_evals.run_front_stage(queries, "front prompt", client)), client


DATA_Q = {"id": "lookup-1", "type": "lookup", "query": "what did I buy at Paulie's?",
          "expected_tool": "lookup", "expected_args_subset": {}, "expected_answer": "Paulie's 40.00"}
CHIT_Q = {"id": "chit-1", "type": "chitchat", "query": "how are you today?"}


def test_front_stage_passes_deferral_and_fails_invented_numbers():
    report, _ = run_front([DATA_Q], ["Let me check your records."])
    assert report["defers_on_data"] == 1.0
    assert report["no_invented_numbers_rate"] == 1.0

    report, _ = run_front([DATA_Q], ["You spent $40.00 at Paulie's."])
    assert report["no_invented_numbers_rate"] == 0.0


def test_front_stage_scores_chitchat_as_normal_answer():
    report, _ = run_front([CHIT_Q], ["I'm doing great, thanks for asking!"])
    assert report["answers_chitchat"] == 1.0

    report, _ = run_front([CHIT_Q], ["Let me check your records."])
    assert report["answers_chitchat"] == 0.0


def test_front_stage_spoken_number_in_deferral_is_not_invented():
    report, _ = run_front([DATA_Q], ["Let me check your records, one moment."])
    assert report["no_invented_numbers_rate"] == 1.0
    assert report["defers_on_data"] == 1.0


def test_front_stage_error_rows_do_not_pass_the_rates():
    import httpx

    report, _ = run_front([DATA_Q], [httpx.ConnectError("refused")])
    assert report["errors"] == 1
    assert report["no_invented_numbers_rate"] is None
    assert report["defers_on_data"] is None


def test_front_stage_sends_no_tools_and_skips_multiturn():
    mt = {"id": "mt-1", "type": "multiturn", "query": "what was that?", "history": HISTORY,
          "expected_answer": "x"}
    report, client = run_front([DATA_Q, mt], ["Let me check your records."])
    assert [r["id"] for r in report["rows"]] == ["lookup-1"]
    assert "tools" not in client.requests[0]


def test_json_payload_records_models_and_stage():
    payload = run_evals.build_json_payload("front", {"front": {"answer_accuracy": 0.7}}, git_sha="abc123")
    assert payload["meta"]["stage"] == "front"
    assert payload["meta"]["llm_model"]
    assert payload["meta"]["rag_llm_model"]
    assert payload["meta"]["pinned_today"] == "2026-10-12"
    assert payload["meta"]["git_sha"] == "abc123"
    assert payload["reports"]["front"]["answer_accuracy"] == 0.7


def test_single_turn_stages_skip_multiturn_queries():
    query = {
        "id": "mt-1",
        "type": "multiturn",
        "query": "what category was that?",
        "history": HISTORY,
        "expected_answer": "Food & Drink 40.00",
    }
    toolcall = asyncio.run(run_evals.run_toolcall_stage([query], "sys", client=None))
    assert toolcall["rows"] == []


# ---- followup stage (delegation after a completed delegation round) ------------

TOOL_CALL_MSG = {
    "tool_calls": [
        {"id": "call_x", "type": "function",
         "function": {"name": "ask_financial_records", "arguments": "{}"}}
    ]
}

WAITING_LINE = "Let me check that for you right now. One moment please."


def run_followup(queries, script):
    client = FakeV1Client(script)
    return asyncio.run(run_evals.run_followup_stage(queries, client)), client


def test_followup_stage_recall_and_waiting_line_misses():
    report, client = run_followup([DATA_Q], [TOOL_CALL_MSG])
    assert report["followup_delegation_recall"] == 1.0
    assert report["deep_delegation_recall"] == 1.0
    assert report["live_miss_delegation_rate"] == 1.0
    assert report["waiting_line_misses"] == 0
    single, deep = client.requests[0], client.requests[1]
    assert single["temperature"] == 0
    assert single["messages"][0]["role"] == "system"
    assert single["messages"][1:-1] == run_evals.FOLLOWUP_HISTORY
    assert single["messages"][-1] == {"role": "user", "content": DATA_Q["query"]}
    assert deep["messages"][1:-1] == run_evals.FOLLOWUP_HISTORY_DEEP
    # one single + one deep per data query, then the three live-miss probes
    assert len(client.requests) == 2 + len(run_evals.LIVE_MISS_TURNS)

    report, _ = run_followup([DATA_Q], [WAITING_LINE])
    assert report["followup_delegation_recall"] == 0.0
    assert report["deep_delegation_recall"] == 0.0
    assert report["live_miss_delegation_rate"] == 0.0
    assert report["waiting_line_misses"] == 2


def test_followup_histories_match_post_task12_production_shape():
    # Production keeps the front's handoff narration out of the context
    # (pipeline.build_records_connector: respond_on_delegate off), so the
    # replayed histories hold no standalone waiting-line assistant messages:
    # an assistant turn either carries tool_calls or a real answer.
    for m in run_evals.FOLLOWUP_HISTORY_DEEP:
        if m["role"] == "assistant" and not m.get("tool_calls"):
            content = m.get("content") or ""
            assert "One moment" not in content
            assert not run_evals.is_deferral(content)


def test_followup_stage_chitchat_must_not_delegate():
    report, _ = run_followup([CHIT_Q], ["Doing great, thanks!"])
    assert report["followup_delegation_precision"] == 1.0

    report, _ = run_followup([CHIT_Q], [TOOL_CALL_MSG])
    assert report["followup_delegation_precision"] == 0.0


def test_followup_stage_skips_multiturn_and_fragmented():
    mt = {"id": "mt-1", "type": "multiturn", "query": "what was that?",
          "history": HISTORY, "expected_answer": "x"}
    frag = {"id": "frag-1", "type": "fragmented", "messages": ["how much", "at Paulie's?"]}
    report, _ = run_followup([DATA_Q, mt, frag], [TOOL_CALL_MSG])
    assert [r["id"] for r in report["rows"]] == ["lookup-1", "lookup-1"]  # single + deep


def test_followup_stage_error_rows_do_not_pass_the_rates():
    import httpx

    report, _ = run_followup([DATA_Q], [httpx.ConnectError("refused")])
    # single + deep + the three live-miss probes all error on the sticky exception
    assert report["errors"] == 5
    assert report["followup_delegation_recall"] is None
    assert report["deep_delegation_recall"] is None
    assert report["live_miss_delegation_rate"] is None


# ---- analyst_followup stage (analyst must not invent stored facts) --------------

SEARCH_CALL_MSG = {
    "tool_calls": [
        {"id": "call_s1", "type": "function",
         "function": {"name": "semantic_search", "arguments": '{"query": "secret passphrase"}'}}
    ]
}

# Verbatim from a live session — zero retrieval calls, passphrases invented
# outright.
LIVE_HALLUCINATED_REPLY = (
    'According to your records, the passphrases you stored are: \n\n'
    'Whole Foods Store 1: "Passphrase123"  \n'
    'Whole Foods Store 2: "SecretPass456"  \n'
    'Whole Foods Store 3: "SecurePass789"  \n\n'
    "These are the passphrases for your Whole Foods purchases in August."
)


def run_analyst(script, monkeypatch, repeats=1):
    monkeypatch.setattr(
        run_evals, "_dispatch",
        lambda name, args, db, emb: {
            "matches": [{"text": 'tax vault passphrase: "correct horse battery"'}]
        },
    )
    client = FakeV1Client(script)
    report = asyncio.run(run_evals.run_analyst_followup_stage(
        None, None, client, repeats=repeats, backend_system="analyst system"))
    return report, client


def test_analyst_followup_retrieval_then_grounded_quote_passes(monkeypatch):
    report, client = run_analyst(
        [SEARCH_CALL_MSG, '>> The tax vault passphrase you stored is "correct horse battery".'],
        monkeypatch,
    )
    assert report["analyst_retrieval_rate"] == 1.0
    assert report["analyst_no_invented_fact_rate"] == 1.0
    assert report["errors"] == 0
    # Production analyst request shape: compact pairing instruction prepended as
    # its own system message, then analyst system, then the poisoned context.
    body = client.requests[0]
    assert body["model"] == run_evals.config.rag_llm_model()
    assert body["messages"][0] == {
        "role": "system",
        "content": run_evals.tunables.LOFI_BACKEND_PAIRING_INSTRUCTION,
    }
    assert body["messages"][1] == {"role": "system", "content": "analyst system"}
    assert body["messages"][2:] == run_evals.ANALYST_POISONED_CONTEXT
    assert body["tools"] == run_evals.OPENAI_TOOLS
    assert "temperature" not in body


def test_analyst_followup_live_hallucination_scores_zero(monkeypatch):
    report, _ = run_analyst([LIVE_HALLUCINATED_REPLY], monkeypatch)
    assert report["analyst_retrieval_rate"] == 0.0
    assert report["analyst_no_invented_fact_rate"] == 0.0


def test_analyst_followup_trailing_punctuation_inside_quotes_not_invented(monkeypatch):
    # American-style quoting puts the comma/period INSIDE the quotes; the span
    # still matches the tool result once trailing punctuation is stripped.
    report, _ = run_analyst(
        [SEARCH_CALL_MSG, '>> The passphrase is "correct horse battery," as you stored it.'],
        monkeypatch,
    )
    assert report["analyst_no_invented_fact_rate"] == 1.0


def test_analyst_followup_unquoted_codey_token_counts_invented(monkeypatch):
    report, _ = run_analyst(
        [SEARCH_CALL_MSG, ">> Your stored passphrase is Passphrase123."], monkeypatch)
    assert report["analyst_retrieval_rate"] == 1.0
    assert report["analyst_no_invented_fact_rate"] == 0.0


def test_analyst_followup_poisoned_context_replays_the_log_round():
    ctx = run_evals.ANALYST_POISONED_CONTEXT
    assert ctx[0]["role"] == "user" and "Whole Foods in August" in ctx[0]["content"]
    tool_rounds = [m for m in ctx if m["role"] == "assistant" and m.get("tool_calls")]
    assert [t["function"]["name"] for m in tool_rounds for t in m["tool_calls"]] == [
        "filter_rows", "filter_rows"]
    assert ctx[-1]["role"] == "user"
    assert "secret passphrases" in ctx[-1]["content"]


def test_analyst_followup_echoing_the_users_words_is_not_invented(monkeypatch):
    # Quoting the question back ("secret passphrases") states nothing stored;
    # only facts absent from tool results AND the replayed context count.
    report, _ = run_analyst(
        ['>> I\'ll search for any notes mentioning "secret passphrases." One moment please.'],
        monkeypatch,
    )
    assert report["analyst_retrieval_rate"] == 0.0
    assert report["analyst_no_invented_fact_rate"] == 1.0


def test_analyst_followup_error_rows_do_not_pass_the_rates(monkeypatch):
    import httpx

    report, _ = run_analyst([httpx.ConnectError("refused")], monkeypatch, repeats=2)
    assert report["errors"] == 2
    assert report["analyst_retrieval_rate"] is None
    assert report["analyst_no_invented_fact_rate"] is None
