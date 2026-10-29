"""Eval harness for the records stack. All stages run against localhost,
no model-authored SQL:

  --stage retrieval  gold args straight into retrieval.py; source-id hit rate.
  --stage toolcall   first-turn tool selection over /v1 (streamed, the
                      production path), incl. chitchat no-tool probes and
                      fragmented multi-message probes mirroring STT chop.
  --stage e2e        full /v1 tool loop (execute retrieval, feed results back,
                      final completion = answer): answer accuracy + latency.
  --stage isolate    re-runs e2e failures with gold rows pasted in as the tool
                      result, to tell retrieval failures from model failures.
  --stage front      a candidate front model: never invents numbers, defers
                      records questions, answers chitchat. Not in --stage all.
  --stage followup   front delegation on replayed production contexts (the
                      live-miss regression set). Not in --stage all.
  --stage analyst_followup
                      a candidate analyst on replayed contexts: real tool
                      calls, no invented facts, latency. Not in --stage all.
  --stage all        (default) the first four, in order.

Questions are grounded against PINNED_TODAY — the gold answers for relative-date
queries in queries.jsonl are computed for that date and rot if it changes.

Everything here is a plain script run against a live Ollama + indexed DB
(``uv run python evals/run_evals.py``); the pure scoring functions below are
covered offline by ``tests/test_evals.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx

import config
import ingest
import rag_agent
import retrieval
import store
import tools
import tunables

QUERIES_PATH = Path(__file__).resolve().parent / "queries.jsonl"

RETRIEVAL_FUNCS = {
    "lookup": retrieval.lookup,
    "filter_rows": retrieval.filter_rows,
    "aggregate": retrieval.aggregate,
    "semantic_search": retrieval.semantic_search,
}

OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": spec["description"],
            "parameters": {
                "type": "object",
                "properties": spec["properties"],
                "required": spec["required"],
            },
        },
    }
    for name, spec in tools.PARAMS.items()
]

PINNED_TODAY = datetime.date(2026, 10, 12)


def load_queries(path=QUERIES_PATH) -> list[dict]:
    lines = Path(path).read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# ---- number extraction / normalization --------------------------------------

_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_NUMBER_WORDS = set(_ONES) | set(_TENS) | {"hundred", "thousand", "million"}

_DIGIT_NUMBER_RE = re.compile(r"-?\$?\d[\d,]*\.?\d*")
_NUMBER_WORD_ALT = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
_NUMBER_PHRASE = rf"(?:(?:{_NUMBER_WORD_ALT})(?:[\s-]+(?:and\s+)?(?:{_NUMBER_WORD_ALT}))*)"
_MONEY_WORDS_RE = re.compile(
    rf"\b({_NUMBER_PHRASE})\s+dollars(?:\s+and\s+({_NUMBER_PHRASE})\s+cents)?\b"
)


def _words_to_int(words: list[str]) -> int | None:
    """Small-English number-word parser: ones/teens/tens + hundred/thousand/
    million, with 'and' as a no-op connector ('one hundred and five')."""
    total = 0
    current = 0
    seen = False
    for raw in words:
        for token in raw.replace("-", " ").split():
            token = token.lower()
            if token == "and":
                continue
            if token in _ONES:
                current += _ONES[token]
                seen = True
            elif token in _TENS:
                current += _TENS[token]
                seen = True
            elif token == "hundred":
                current = (current or 1) * 100
                seen = True
            elif token in ("thousand", "million"):
                scale = 1000 if token == "thousand" else 1_000_000
                total += (current or 1) * scale
                current = 0
                seen = True
            else:
                return None
    if not seen:
        return None
    return total + current


def _spoken_money_numbers(text: str) -> list[float]:
    numbers = []
    for match in _MONEY_WORDS_RE.finditer(text):
        dollars = _words_to_int(match.group(1).split())
        if dollars is None:
            continue
        cents_words = match.group(2)
        cents = _words_to_int(cents_words.split()) if cents_words else 0
        numbers.append(round(dollars + (cents or 0) / 100, 2))
    return numbers


def _spoken_plain_numbers(text: str) -> list[float]:
    tokens = re.findall(r"[a-z]+", text.lower())
    numbers = []
    i, n = 0, len(tokens)
    while i < n:
        if tokens[i] in _NUMBER_WORDS:
            j = i
            run = []
            while j < n and (
                tokens[j] in _NUMBER_WORDS
                or (tokens[j] == "and" and j + 1 < n and tokens[j + 1] in _NUMBER_WORDS)
            ):
                run.append(tokens[j])
                j += 1
            value = _words_to_int(run)
            if value is not None:
                numbers.append(float(value))
            i = j
        else:
            i += 1
    return numbers


def extract_numbers(text) -> list[float]:
    """Every number findable in text — digit form or spoken words — as
    magnitudes (sign stripped: a spoken answer never says 'negative')."""
    text = str(text).lower()
    numbers = []
    for match in _DIGIT_NUMBER_RE.finditer(text):
        raw = match.group().replace("$", "").replace(",", "")
        try:
            numbers.append(abs(float(raw)))
        except ValueError:
            continue
    numbers.extend(_spoken_money_numbers(text))
    numbers.extend(_spoken_plain_numbers(text))
    return numbers


def primary_number(text) -> float | None:
    """The single number a gold string is 'about': the first decimal-form
    number if there is one (amounts), else the first bare integer (counts) —
    gold strings put that number right after the merchant/verb, before any
    date digits."""
    text = str(text)
    decimal = re.search(r"-?\d[\d,]*\.\d+", text)
    if decimal:
        return abs(float(decimal.group().replace(",", "")))
    integer = re.search(r"-?\d[\d,]*", text)
    if integer:
        return abs(float(integer.group().replace(",", "")))
    return None


def numbers_close(a: float, b: float, tol: float = 0.02) -> bool:
    return abs(a - b) <= max(tol, abs(b) * 0.02)


# ---- text normalization / containment ---------------------------------------

_STOPWORDS = {"the", "a", "an", "at", "on", "in", "of", "to", "for", "and"}


def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    return " ".join(text.split())


def _significant_tokens(text: str) -> list[str]:
    words = normalize_text(text).split()
    return [w for w in words if len(w) > 2 and w not in _STOPWORDS and not w.isdigit()]


REFUSAL_PHRASES = (
    "couldn't find",
    "could not find",
    "don't have",
    "do not have",
    "no record",
    "not in your records",
    "not in the records",
    "nothing about",
    "no transactions",
)


def is_refusal(text: str) -> bool:
    normalized = normalize_text(text)
    return any(phrase in text.lower() or normalize_text(phrase) in normalized for phrase in REFUSAL_PHRASES)


def answer_contains(expected: str, actual: str) -> bool:
    """Heuristic containment: the actual (spoken) answer names the same thing
    the gold string does — matching text tokens (merchant/passphrase words)
    and, when the gold has a number, a close numeric match."""
    expected_number = primary_number(expected)
    text_tokens = _significant_tokens(expected)
    actual_normalized = normalize_text(actual)

    if text_tokens:
        hits = sum(1 for t in text_tokens if t in actual_normalized)
        if hits < max(1, round(len(text_tokens) * 0.6)):
            return False

    if expected_number is not None:
        actual_numbers = extract_numbers(actual)
        if not any(numbers_close(n, expected_number) for n in actual_numbers):
            return False

    return bool(text_tokens) or expected_number is not None


# ---- args-subset matching ----------------------------------------------------


def _normalize_value(value):
    if isinstance(value, str):
        return value.strip().lower()
    return value


def args_subset(expected: dict, actual: dict) -> bool:
    """True if every key in expected is present in actual with an equal
    (case-insensitive for strings) value. 'filters' lists are compared as
    order-independent sets of subset-matching dicts."""
    for key, expected_value in expected.items():
        if key not in actual:
            return False
        actual_value = actual[key]
        if key == "filters" and isinstance(expected_value, list):
            if not isinstance(actual_value, list) or len(actual_value) != len(expected_value):
                return False
            remaining = list(actual_value)
            for exp_filter in expected_value:
                match = next((a for a in remaining if args_subset(exp_filter, a)), None)
                if match is None:
                    return False
                remaining.remove(match)
            continue
        if _normalize_value(expected_value) != _normalize_value(actual_value):
            return False
    return True


# ---- source-id matching -------------------------------------------------------


def source_ids_from_result(result: dict) -> list:
    if "rows" in result:
        return [row["id"] for row in result["rows"]]
    if "results" in result:
        ids = []
        for item in result["results"]:
            if item.get("chunk_type") == "csv_row" and item.get("row_id") is not None:
                ids.append(item["row_id"])
            else:
                # doc/rollup chunks have no row_id — the basename is the
                # queries.jsonl convention for their expected_source_ids.
                ids.append(Path(item["source"]).name)
        return ids
    return []


def source_id_hit(expected_ids: list, actual_ids: list) -> bool:
    if not expected_ids:
        return True
    actual_set = set(actual_ids)
    return all(eid in actual_set for eid in expected_ids)


# ---- stage: retrieval (no LLM) ------------------------------------------------


def run_retrieval_stage(queries: list[dict], db_path, embedder) -> dict:
    per_type: dict[str, list[bool]] = {}
    rows = []
    for q in queries:
        if "expected_tool" not in q:
            continue
        func = RETRIEVAL_FUNCS[q["expected_tool"]]
        kwargs = dict(q["expected_args_subset"])
        if q["expected_tool"] == "semantic_search":
            kwargs["embedder"] = embedder
        try:
            result = func(db_path, **kwargs)
            hit = source_id_hit(q["expected_source_ids"], source_ids_from_result(result))
        except (TypeError, ValueError) as exc:
            hit = False
            result = {"error": str(exc)}
        per_type.setdefault(q["type"], []).append(hit)
        rows.append({"id": q["id"], "type": q["type"], "hit": hit})
    accuracy = {t: sum(hits) / len(hits) for t, hits in per_type.items()}
    return {"rows": rows, "accuracy_by_type": accuracy}


# ---- stage: toolcall (first-turn tool selection over /v1) ---------------------


def _v1_url() -> str:
    return f"{config.ollama_base_url().rstrip('/')}/chat/completions"


async def _first_turn(user_messages: list[str], system: str, client: httpx.AsyncClient) -> dict:
    body = {
        "model": config.llm_model(),
        "messages": [
            {"role": "system", "content": system},
            *({"role": "user", "content": m} for m in user_messages),
        ],
        "tools": OPENAI_TOOLS,
        "stream": True,
        "reasoning_effort": "none",
    }
    started = time.perf_counter()
    ttft_ms = None
    tool_name = None
    arguments = ""
    async with client.stream("POST", _v1_url(), json=body) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                break
            chunk = json.loads(payload)
            if ttft_ms is None:
                ttft_ms = (time.perf_counter() - started) * 1000
            delta = chunk["choices"][0]["delta"]
            for call in delta.get("tool_calls") or []:
                function = call.get("function", {})
                tool_name = function.get("name") or tool_name
                arguments += function.get("arguments", "")
    args = None
    if arguments:
        try:
            args = json.loads(arguments)
        except json.JSONDecodeError:
            args = None
    return {"tool": tool_name, "args": args, "ttft_ms": ttft_ms}


async def run_toolcall_stage(queries: list[dict], system: str, client: httpx.AsyncClient) -> dict:
    rows = []
    for q in queries:
        if q["type"] == "multiturn":
            continue
        user_messages = q["messages"] if q["type"] == "fragmented" else [q["query"]]
        try:
            first = await _first_turn(user_messages, system, client)
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            first = {"tool": None, "args": None, "ttft_ms": None, "error": repr(exc)}
        row = {"id": q["id"], "type": q["type"], "first": first}
        if "expected_tool" in q:
            row["correct_tool"] = first["tool"] == q["expected_tool"]
            row["args_subset_ok"] = bool(first["args"]) and args_subset(
                q["expected_args_subset"], first["args"]
            )
        rows.append(row)

    data_rows = [r for r in rows if r["type"] not in ("chitchat", "fragmented")]
    chitchat_rows = [r for r in rows if r["type"] == "chitchat"]
    fragmented_rows = [r for r in rows if r["type"] == "fragmented"]
    ttfts = [r["first"]["ttft_ms"] for r in rows if r["first"].get("ttft_ms")]
    return {
        "rows": rows,
        "correct_tool_rate": (
            sum(r["correct_tool"] for r in data_rows) / len(data_rows) if data_rows else None
        ),
        "args_subset_rate": (
            sum(r["args_subset_ok"] for r in data_rows) / len(data_rows) if data_rows else None
        ),
        "no_tool_on_chitchat": (
            sum(r["first"]["tool"] is None for r in chitchat_rows) / len(chitchat_rows)
            if chitchat_rows
            else None
        ),
        "fragmented_tool_recall": (
            sum(r["first"]["tool"] is not None for r in fragmented_rows) / len(fragmented_rows)
            if fragmented_rows
            else None
        ),
        "first_turn_ttft_ms_avg": round(sum(ttfts) / len(ttfts), 1) if ttfts else None,
    }


# ---- stage: e2e (full /v1 tool loop) ------------------------------------------


def _dispatch(name: str, args: dict, db_path, embedder) -> dict:
    funcs = {
        "lookup": retrieval.lookup,
        "filter_rows": retrieval.filter_rows,
        "aggregate": retrieval.aggregate,
    }
    if name == "semantic_search":
        return retrieval.semantic_search(db_path, embedder=embedder, **args)
    if name not in funcs:
        raise ValueError(f"unknown tool {name!r}")
    return funcs[name](db_path, **args)


async def _e2e_loop(
    query: str, system: str, db_path, embedder, client: httpx.AsyncClient, max_rounds: int = 4
) -> dict:
    import sqlite3

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": query},
    ]
    traces = []
    timings = {"rounds": 0, "llm_ms": 0.0, "tools_ms": 0.0, "total_ms": 0.0}
    started = time.perf_counter()
    answer = ""
    error = None
    for round_index in range(max_rounds + 1):
        body = {
            "model": config.llm_model(),
            "messages": messages,
            "stream": False,
            "reasoning_effort": "none",
        }
        if round_index < max_rounds:
            body["tools"] = OPENAI_TOOLS
        timings["rounds"] += 1
        llm_start = time.perf_counter()
        try:
            response = await client.post(_v1_url(), json=body)
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            error = repr(exc)
            break
        timings["llm_ms"] += (time.perf_counter() - llm_start) * 1000
        message = response.json()["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        if not calls or round_index >= max_rounds:
            answer = message.get("content") or ""
            break
        messages.append(message)
        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = None
            tool_start = time.perf_counter()
            if not isinstance(args, dict):
                result = {"error": "arguments must be a JSON object"}
            else:
                try:
                    result = _dispatch(name, args, db_path, embedder)
                except (TypeError, ValueError, KeyError, sqlite3.Error) as exc:
                    result = {"error": str(exc)}
            timings["tools_ms"] += (time.perf_counter() - tool_start) * 1000
            traces.append({"tool": name, "args": args, "error": result.get("error")})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", f"call-{round_index}"),
                    "content": json.dumps(result, default=str),
                }
            )
    timings["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return {"answer": answer, "traces": traces, "timings": timings, "error": error}


async def run_e2e_stage(
    queries: list[dict], system: str, db_path, embedder, client: httpx.AsyncClient
) -> dict:
    rows = []
    for q in queries:
        if "expected_tool" not in q:
            continue
        result = await _e2e_loop(q["query"], system, db_path, embedder, client)
        if q.get("refusal"):
            answer_ok = is_refusal(result["answer"])
        else:
            answer_ok = answer_contains(q["expected_answer"], result["answer"])
        rows.append(
            {
                "id": q["id"],
                "type": q["type"],
                "answer": result["answer"],
                "answer_ok": answer_ok,
                "timings": result["timings"],
                "traces": result["traces"],
                "error": result["error"],
            }
        )
    answer_accuracy = sum(r["answer_ok"] for r in rows) / len(rows) if rows else None
    return {"rows": rows, "answer_accuracy": answer_accuracy}


# ---- stage: isolate (retrieval vs. model failure) ----------------------------


async def run_isolate_stage(
    failures: list[dict], queries_by_id: dict, system: str, db_path, embedder, client: httpx.AsyncClient
) -> dict:
    rows = []
    for failure in failures:
        q = queries_by_id[failure["id"]]
        func = RETRIEVAL_FUNCS[q["expected_tool"]]
        kwargs = dict(q["expected_args_subset"])
        if q["expected_tool"] == "semantic_search":
            kwargs["embedder"] = embedder
        gold_result = func(db_path, **kwargs)

        body = {
            "model": config.llm_model(),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": q["query"]},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-gold",
                            "type": "function",
                            "function": {
                                "name": q["expected_tool"],
                                "arguments": json.dumps(q["expected_args_subset"]),
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-gold",
                    "content": json.dumps(gold_result, default=str),
                },
            ],
            "stream": False,
            "reasoning_effort": "none",
        }
        try:
            response = await client.post(_v1_url(), json=body)
            response.raise_for_status()
            answer = response.json()["choices"][0]["message"].get("content") or ""
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            answer = f"(no answer: {exc!r})"
        passed = (
            is_refusal(answer) if q.get("refusal") else answer_contains(q["expected_answer"], answer)
        )
        rows.append(
            {
                "id": q["id"],
                "answer_with_gold_rows": answer,
                "passed_with_gold_rows": passed,
                "verdict": "retrieval failure" if passed else "model failure",
            }
        )
    return {"rows": rows}


# ---- stage: split (front delegation + records agent) --------------------------

DELEGATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": tunables.DELEGATE_TOOL_NAME,
        "description": tunables.DELEGATE_TOOL_DESCRIPTION,
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


# ---- stage: followup (delegation after a completed delegation round) -----------

# Production context shape after one completed delegation: delegate tool call
# → tool ack → developer Backend notes → spoken relay. Captured from a live
# smoke-test session, which also held a spoken waiting narration after each
# tool ack — those taught the temp-0 front to answer records questions with
# waiting lines and no call. Narrations stay out of the context now
# (pipeline.build_records_connector: respond_on_delegate off), so these
# histories replay that shape.
FOLLOWUP_HISTORY = [
    {"role": "assistant", "content": tunables.LOFI_GREETING},
    {
        "role": "user",
        "content": "Can you tell me how many Whole Foods transactions I had in August of 2026?",
    },
    {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call_0001",
                "type": "function",
                "function": {"name": tunables.DELEGATE_TOOL_NAME, "arguments": "{}"},
            }
        ],
    },
    {
        "role": "tool",
        "content": '{"status": "delegated", "backend": "idle"}',
        "tool_call_id": "call_0001",
    },
    {
        "role": "developer",
        "content": "Backend (working): aggregate(filters=[{'column': 'merchant', "
        "'operator': 'eq', 'value': 'Whole Foods'}, {'column': 'date', 'operator': "
        "'between', 'value': '2026-08-01', 'value2': '2026-08-31'}], operation='count')",
    },
    {"role": "developer", "content": "Backend: You had 3 transactions at Whole Foods in August 2026."},
    {
        "role": "assistant",
        "content": "In August 2026, you had 3 transactions at Whole Foods. "
        "Is there anything else you'd like to know about your spending?",
    },
]

# Second completed round + chitchat exchange, continuing the same session. One
# round barely moves delegation; this depth is where the live session missed
# (temperature 0, qwen2.5:7b).
FOLLOWUP_HISTORY_DEEP = FOLLOWUP_HISTORY + [
    {"role": "user", "content": "Um,   Yeah.   What's the total of the transactions?"},
    {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call_0002",
                "type": "function",
                "function": {"name": tunables.DELEGATE_TOOL_NAME, "arguments": "{}"},
            }
        ],
    },
    {
        "role": "tool",
        "content": '{"status": "delegated", "backend": "idle"}',
        "tool_call_id": "call_0002",
    },
    {
        "role": "developer",
        "content": "Backend (working): aggregate(filters=[{'column': 'merchant', "
        "'operator': 'eq', 'value': 'Whole Foods'}, {'column': 'date', 'operator': "
        "'between', 'value': '2026-08-01', 'value2': '2026-08-31'}], operation='sum', "
        "include_transfers=False)",
    },
    {
        "role": "developer",
        "content": "Backend: The total of your Whole Foods transactions in August 2026 was $301.81.",
    },
    {
        "role": "assistant",
        "content": "The total of your Whole Foods transactions in August 2026 was three "
        "hundred and one dollars and eighty-one cents. Is there anything else you'd "
        "like to know about your spending?",
    },
    {"role": "user", "content": "Yeah."},
    {"role": "user", "content": "Hmm."},
    {"role": "assistant", "content": "Of course! What would you like to know next?"},
]

# The three turns the live front failed to delegate on (verbatim transcripts).
LIVE_MISS_TURNS = [
    "Tell me.   What?   Did I spend, what's the thing that I...   spent the most "
    "on in August of 2026.   Like what item?   or laminatum.",
    "I want all specific types of transactions, so do like a strict...   stream match.",
    "Are you still there?  You're not looking.",
]


async def _followup_delegation(
    question: str, client: httpx.AsyncClient, history: list[dict] = FOLLOWUP_HISTORY
) -> dict:
    body = {
        "model": config.llm_model(),
        "messages": [
            {
                "role": "system",
                "content": tunables.SPLIT_FRONT_PROMPT
                + "\n\n"
                + tunables.LOFI_FRONTEND_PAIRING_INSTRUCTION,
            },
            *history,
            {"role": "user", "content": question},
        ],
        "tools": [DELEGATE_SCHEMA],
        # Production front runs at 0 (pipeline.build_pipeline).
        "temperature": 0,
        "reasoning_effort": "none",
        "stream": False,
    }
    started = time.perf_counter()
    response = await client.post(_v1_url(), json=body)
    response.raise_for_status()
    message = response.json()["choices"][0]["message"]
    return {
        "delegated": bool(message.get("tool_calls")),
        "answer": message.get("content") or "",
        "ms": round((time.perf_counter() - started) * 1000, 1),
    }


async def run_followup_stage(queries: list[dict], client: httpx.AsyncClient) -> dict:
    """Front delegation on turns AFTER completed delegation rounds: data
    questions must still call the tool; chitchat must still answer without it.
    Each data query is probed at both history depths; the live-miss turns run
    on the deep history they failed on."""

    async def probe(question, history, row):
        try:
            result = await _followup_delegation(question, client, history)
            result["error"] = None
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            result = {"delegated": None, "answer": "", "ms": None, "error": repr(exc)}
        return {**row, **result}

    rows = []
    live_rows = []
    for q in queries:
        if q["type"] in ("multiturn", "fragmented"):
            continue
        rows.append(await probe(q["query"], FOLLOWUP_HISTORY, {"id": q["id"], "type": q["type"], "depth": "single"}))
        if q["type"] != "chitchat":
            rows.append(await probe(q["query"], FOLLOWUP_HISTORY_DEEP, {"id": q["id"], "type": q["type"], "depth": "deep"}))
    for i, turn in enumerate(LIVE_MISS_TURNS):
        live_rows.append(await probe(turn, FOLLOWUP_HISTORY_DEEP, {"id": f"live-miss-{i + 1}", "depth": "deep"}))

    scored = [r for r in rows if not r["error"]]
    data = [r for r in scored if r["type"] != "chitchat" and r["depth"] == "single"]
    deep = [r for r in scored if r["type"] != "chitchat" and r["depth"] == "deep"]
    chit = [r for r in scored if r["type"] == "chitchat"]
    live = [r for r in live_rows if not r["error"]]
    mss = [r["ms"] for r in scored if r["ms"]]
    return {
        "rows": rows,
        "live_rows": live_rows,
        "errors": sum(bool(r["error"]) for r in rows + live_rows),
        "followup_delegation_recall": (
            sum(bool(r["delegated"]) for r in data) / len(data) if data else None
        ),
        "deep_delegation_recall": (
            sum(bool(r["delegated"]) for r in deep) / len(deep) if deep else None
        ),
        "live_miss_delegation_rate": (
            sum(bool(r["delegated"]) for r in live) / len(live) if live else None
        ),
        "followup_delegation_precision": (
            sum(r["delegated"] is False for r in chit) / len(chit) if chit else None
        ),
        "waiting_line_misses": sum(
            1 for r in data + deep if not r["delegated"] and is_deferral(r["answer"])
        ),
        "front_ms_avg": round(sum(mss) / len(mss), 1) if mss else None,
    }


# ---- stage: analyst_followup (analyst must not invent stored facts) -------------

# Delegated-request trailer the backend worker appends to each transcript
# (pipecat backend_llm_worker.TRANSCRIPT_REQUEST_INSTRUCTION, frozen here as
# part of the verbatim log replay).
TRANSCRIPT_REQUEST = (
    "Act on the user's most recent request in the conversation above. If it "
    "asks for something, tell the user the result as soon as you have it, "
    "before going on with other work, in a message that begins with >>. If it "
    "only stops or changes work already under way, tell them nothing: the "
    "assistant already has."
)

# The analyst's own context at the moment it invented passphrases, verbatim
# from a live session: round 1's transcript request, working narration,
# filter_rows error + retry, the three Whole Foods rows and the final answer,
# then round 2's passphrase request. Live, the analyst answered round 2 with
# ZERO retrieval calls — round 1's transaction rows in context looked like
# grounds. The stage replays this context and demands retrieval.
ANALYST_POISONED_CONTEXT = [
    {
        "role": "user",
        "content": (
            "Voice conversation so far:\nASSISTANT: Hi. I'm your private, offline "
            "financial thinking partner. What's on your mind today?\nUSER: Hey, "
            "what is...   how many   Time said I go to Whole Foods in August.\n\n"
            + TRANSCRIPT_REQUEST
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Leban\n\n>> To find out how many times you went to Whole Foods in "
            "August, I'll search your transactions for Whole Foods and filter by "
            "that month."
        ),
    },
    {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call_3scuzxgf",
                "type": "function",
                "function": {
                    "name": "filter_rows",
                    "arguments": '{"limit": 5, "order_by": "date", "column": "merchant", "value": "Whole Foods", "filters": [{"column": "date", "operator": "between", "value": "2026-08-01", "value2": "2026-08-31"}]}',
                },
            }
        ],
    },
    {
        "role": "tool",
        "content": '{"error": "filter_rows() missing 1 required positional argument: \'operator\'"}',
        "tool_call_id": "call_3scuzxgf",
    },
    {
        "role": "assistant",
        "content": (
            "To look up your Whole Foods visits in August, I need to specify how "
            "to order the results. Let me set that to sorting by date, which is "
            "the default."
        ),
    },
    {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call_xbghw9vs",
                "type": "function",
                "function": {
                    "name": "filter_rows",
                    "arguments": '{"column": "date", "operator": "between", "value": "2026-08-01", "value2": "2026-08-31", "filters": [{"column": "merchant", "operator": "eq", "value": "Whole Foods"}], "limit": 5}',
                },
            }
        ],
    },
    {
        "role": "tool",
        "content": '{"rows": [{"id": 253152574663202619, "date": "2026-08-25", "merchant": "Whole Foods", "category": "Groceries", "account": "JetBlue (...6769)", "statement": "Whole Foods ABS 10143", "notes": "", "amount": -65.27, "tags": "", "owner": "Shared", "reviewed": ""}, {"id": 251885673541703604, "date": "2026-08-10", "merchant": "Whole Foods", "category": "Groceries", "account": "JetBlue (...6769)", "statement": "WHOLEFDS ABS 10143", "notes": "", "amount": -114.45, "tags": "", "owner": "Shared", "reviewed": ""}, {"id": 251250765966251829, "date": "2026-08-03", "merchant": "Whole Foods", "category": "Groceries", "account": "JetBlue (...6769)", "statement": "WHOLEFDS ABS 10143", "notes": "", "amount": -122.09, "tags": "", "owner": "Shared", "reviewed": ""}], "elapsed_ms": 27.51}',
        "tool_call_id": "call_xbghw9vs",
    },
    {
        "role": "assistant",
        "content": (
            "You visited Whole Foods three times in August. The dates were "
            "August 3, August 10, and August 25. The amounts spent were $122.09, "
            "$114.45, and $65.27, respectively."
        ),
    },
    {
        "role": "user",
        "content": (
            "Voice conversation since the previous delegation:\nASSISTANT: Let me "
            "check your records for how many times you visited Whole Foods in "
            "August. It might take a moment.\nUSER: You still there?\nASSISTANT: "
            "You visited Whole Foods three times in August. The dates were August "
            "3, August 10, and August 25. You spent a total of three hundred and "
            "one dollars and eighty-one cents during these visits.\nUSER: Can you "
            "tell me what those secret passphrases is?\n\n" + TRANSCRIPT_REQUEST
        ),
    },
]

# Stored-fact candidates in a spoken answer: double/curly-quoted spans plus bare
# password-shaped tokens (letters then 2+ digits, e.g. Passphrase123).
_QUOTED_SPAN_RE = re.compile(r'["“]([^"“”]{3,})["”]')
_CODEY_TOKEN_RE = re.compile(r"\b[A-Za-z]+\d{2,}\w*\b")


def invented_facts(answer: str, tool_results_text: str) -> list[str]:
    """Stored-fact candidates in the answer absent from every tool result.
    Strips trailing punctuation — American-style quoting puts it inside the
    quotes ('"BLUE MANATEE 436921,"')."""
    hay = tool_results_text.lower()
    candidates = _QUOTED_SPAN_RE.findall(answer) + _CODEY_TOKEN_RE.findall(answer)
    return [c for c in (c.strip(" .,;:!?") for c in candidates) if c and c.lower() not in hay]


async def _analyst_turn(
    client: httpx.AsyncClient, backend_system: str, db_path, embedder, max_rounds: int = 6
) -> dict:
    import sqlite3

    messages = [
        {"role": "system", "content": tunables.LOFI_BACKEND_PAIRING_INSTRUCTION},
        {"role": "system", "content": backend_system},
        *ANALYST_POISONED_CONTEXT,
    ]
    tool_names: list[str] = []
    tool_results: list[str] = []
    answer = ""
    started = time.perf_counter()
    for round_index in range(max_rounds):
        # Production backend shape: rag model, no temperature/reasoning override.
        # list() snapshots the request body against later in-place appends.
        body = {
            "model": config.rag_llm_model(),
            "messages": list(messages),
            "tools": OPENAI_TOOLS,
            "stream": False,
        }
        response = await client.post(_v1_url(), json=body)
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        if not calls or round_index >= max_rounds - 1:
            answer = rag_agent.strip_think(message.get("content") or "").strip()
            break
        messages.append(message)
        for call in calls:
            name = call["function"]["name"]
            tool_names.append(name)
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = None
            if not isinstance(args, dict):
                result = {"error": "arguments must be a JSON object"}
            else:
                try:
                    result = _dispatch(name, args, db_path, embedder)
                except (TypeError, ValueError, KeyError, sqlite3.Error) as exc:
                    result = {"error": str(exc)}
            content = json.dumps(result, default=str)
            tool_results.append(content)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", f"call-{round_index}"),
                    "content": content,
                }
            )
    return {
        "answer": answer,
        "tools": tool_names,
        "results_text": "\n".join(tool_results),
        "ms": round((time.perf_counter() - started) * 1000, 1),
    }


async def run_analyst_followup_stage(
    db_path, embedder, client: httpx.AsyncClient, repeats: int = 5, backend_system: str | None = None
) -> dict:
    """Analyst grounding on the poisoned context: the passphrase question must
    trigger retrieval, and the answer must not state a stored fact no tool
    returned this session."""
    if backend_system is None:
        grounding = rag_agent.grounded_question("", db_path).rsplit(
            tunables.QUESTION_PREFIX.rstrip(" "), 1
        )[0]
        backend_system = f"{tunables.ANALYST_SYSTEM_PROMPT}\n\n{grounding}"
    rows = []
    for i in range(repeats):
        try:
            result = await _analyst_turn(client, backend_system, db_path, embedder)
            result["error"] = None
            # Grounds = this turn's tool results plus the replayed context
            # (round-1 tool returns and the user's own words are not inventions).
            result["invented"] = invented_facts(
                result["answer"],
                result["results_text"] + "\n" + json.dumps(ANALYST_POISONED_CONTEXT),
            )
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            result = {
                "answer": "", "tools": [], "results_text": "",
                "ms": None, "error": repr(exc), "invented": None,
            }
        rows.append({"id": f"passphrase-replay-{i + 1}", **result})
    ok = [r for r in rows if not r["error"]]
    mss = [r["ms"] for r in ok if r["ms"]]
    return {
        "rows": rows,
        "errors": len(rows) - len(ok),
        "analyst_retrieval_rate": (
            sum(bool(r["tools"]) for r in ok) / len(ok) if ok else None
        ),
        "analyst_no_invented_fact_rate": (
            sum(not r["invented"] for r in ok) / len(ok) if ok else None
        ),
        "analyst_ms_avg": round(sum(mss) / len(mss), 1) if mss else None,
    }


# ---- stage: front (split front model: defer, never invent) --------------------

DEFER_PHRASES = (
    "check your records",
    "let me check",
    "look that up",
    "pull that up",
    "records analyst",
)


def is_deferral(text: str) -> bool:
    normalized = normalize_text(text)
    return any(normalize_text(p) in normalized for p in DEFER_PHRASES)


async def run_front_stage(queries: list[dict], system: str, client) -> dict:
    """The split front alone, no tools: data questions must get a deferral
    with no invented numbers; chitchat must get a normal answer."""
    rows = []
    for q in queries:
        if q["type"] == "multiturn":
            continue
        user_messages = q["messages"] if q["type"] == "fragmented" else [q["query"]]
        body = {
            "model": config.llm_model(),
            "messages": [
                {"role": "system", "content": system},
                *({"role": "user", "content": m} for m in user_messages),
            ],
            "stream": False,
            "reasoning_effort": "none",
        }
        started = time.perf_counter()
        try:
            response = await client.post(_v1_url(), json=body)
            response.raise_for_status()
            answer = response.json()["choices"][0]["message"].get("content") or ""
            error = None
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            answer, error = "", repr(exc)
        rows.append(
            {
                "id": q["id"],
                "type": q["type"],
                "answer": answer,
                "defers": is_deferral(answer),
                # Digit numbers only: the front is prompted to write amounts as
                # digits, and spoken-word numbers ("one moment") aren't amounts.
                "invented_numbers": bool(_DIGIT_NUMBER_RE.search(answer)),
                "ms": round((time.perf_counter() - started) * 1000, 1),
                "error": error,
            }
        )
    # Errored rows prove nothing — exclude them from every rate, count them.
    scored = [r for r in rows if not r["error"]]
    data = [r for r in scored if r["type"] != "chitchat"]
    chit = [r for r in scored if r["type"] == "chitchat"]
    mss = [r["ms"] for r in scored if r["ms"]]
    return {
        "rows": rows,
        "errors": sum(bool(r["error"]) for r in rows),
        "no_invented_numbers_rate": (
            sum(not r["invented_numbers"] for r in data) / len(data) if data else None
        ),
        "defers_on_data": sum(r["defers"] for r in data) / len(data) if data else None,
        "answers_chitchat": (
            sum(bool(r["answer"]) and not r["defers"] for r in chit) / len(chit) if chit else None
        ),
        "front_ms_avg": round(sum(mss) / len(mss), 1) if mss else None,
    }


# ---- report printing ----------------------------------------------------------


def _print_table(title: str, rows: list[tuple]) -> None:
    print(f"\n== {title} ==")
    for row in rows:
        print(" | ".join(str(c) for c in row))


def print_report(retrieval_report=None, toolcall_report=None, e2e_report=None, isolate_report=None, front_report=None, followup_report=None, analyst_followup_report=None) -> None:
    if analyst_followup_report is not None:
        _print_table(
            "Analyst follow-up (poisoned context: retrieve, never invent)",
            [
                ("analyst_retrieval_rate", analyst_followup_report["analyst_retrieval_rate"]),
                ("analyst_no_invented_fact_rate", analyst_followup_report["analyst_no_invented_fact_rate"]),
                ("errors", analyst_followup_report["errors"]),
                ("analyst_ms_avg", analyst_followup_report["analyst_ms_avg"]),
            ],
        )
    if followup_report is not None:
        _print_table(
            "Follow-up delegation (turns after completed delegation rounds)",
            [
                ("followup_delegation_recall", followup_report["followup_delegation_recall"]),
                ("deep_delegation_recall", followup_report["deep_delegation_recall"]),
                ("live_miss_delegation_rate", followup_report["live_miss_delegation_rate"]),
                ("followup_delegation_precision", followup_report["followup_delegation_precision"]),
                ("waiting_line_misses", followup_report["waiting_line_misses"]),
                ("errors", followup_report["errors"]),
                ("front_ms_avg", followup_report["front_ms_avg"]),
            ],
        )
    if front_report is not None:
        _print_table(
            "Split front alone (no tools: defer, never invent)",
            [
                ("no_invented_numbers_rate", front_report["no_invented_numbers_rate"]),
                ("defers_on_data", front_report["defers_on_data"]),
                ("answers_chitchat", front_report["answers_chitchat"]),
                ("errors", front_report["errors"]),
                ("front_ms_avg", front_report["front_ms_avg"]),
            ],
        )
    if retrieval_report is not None:
        _print_table(
            "Retrieval accuracy by type",
            [(t, f"{acc:.0%}") for t, acc in retrieval_report["accuracy_by_type"].items()],
        )
    if toolcall_report is not None:
        _print_table(
            "First-turn tool selection",
            [
                ("correct_tool_rate", toolcall_report["correct_tool_rate"]),
                ("args_subset_rate", toolcall_report["args_subset_rate"]),
                ("no_tool_on_chitchat", toolcall_report["no_tool_on_chitchat"]),
                ("fragmented_tool_recall", toolcall_report["fragmented_tool_recall"]),
                ("first_turn_ttft_ms_avg", toolcall_report["first_turn_ttft_ms_avg"]),
            ],
        )
    if e2e_report is not None:
        _print_table(
            "Answer accuracy (full /v1 tool loop)",
            [("answer_accuracy", e2e_report["answer_accuracy"])],
        )
        _print_table(
            "Latency (ms): loop total (llm | tools)",
            [
                (
                    r["id"],
                    r["timings"].get("total_ms"),
                    round(r["timings"].get("llm_ms", 0)),
                    round(r["timings"].get("tools_ms", 0)),
                )
                for r in e2e_report["rows"]
            ],
        )
    if isolate_report is not None:
        _print_table(
            "Isolate (gold rows pasted in as the tool result)",
            [(r["id"], r["verdict"]) for r in isolate_report["rows"]],
        )


# ---- machine-readable output ---------------------------------------------------


def build_json_payload(stage: str, reports: dict, git_sha: str | None = None) -> dict:
    """Stage reports plus the config they were measured under."""
    return {
        "meta": {
            "stage": stage,
            "llm_model": config.llm_model(),
            "rag_llm_model": config.rag_llm_model(),
            "architecture": config.architecture(),
            "pinned_today": PINNED_TODAY.isoformat(),
            "git_sha": git_sha,
        },
        "reports": reports,
    }


def _git_sha() -> str | None:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=REPO_ROOT, timeout=5,
        )
        return out.stdout.strip() or None
    except OSError:
        return None


# ---- main ----------------------------------------------------------------------


async def _amain(args) -> None:
    queries = load_queries()
    db_path = config.rag_db_path()
    embedder = ingest.OllamaEmbedder(config.ollama_api_url(), config.embed_model())
    queries_by_id = {q["id"]: q for q in queries}

    system = tunables.system_prompt(
        PINNED_TODAY, store.date_range(db_path), store.categories(db_path)
    )

    retrieval_report = toolcall_report = e2e_report = isolate_report = front_report = followup_report = analyst_followup_report = None
    run_all = args.stage == "all"

    if run_all or args.stage == "retrieval":
        retrieval_report = run_retrieval_stage(queries, db_path, embedder)

    async with httpx.AsyncClient(timeout=180.0) as client:
        if run_all or args.stage == "toolcall":
            toolcall_report = await run_toolcall_stage(queries, system, client)
        if run_all or args.stage == "e2e":
            e2e_report = await run_e2e_stage(queries, system, db_path, embedder, client)
        if run_all or args.stage == "isolate":
            if e2e_report is None:
                e2e_report = await run_e2e_stage(queries, system, db_path, embedder, client)
            failures = [r for r in e2e_report["rows"] if not r["answer_ok"]]
            isolate_report = await run_isolate_stage(
                failures, queries_by_id, system, db_path, embedder, client
            )
        if args.stage == "front":
            front_report = await run_front_stage(
                queries, tunables.SPLIT_FRONT_PROMPT, client
            )
        if args.stage == "followup":
            followup_report = await run_followup_stage(queries, client)
        if args.stage == "analyst_followup":
            analyst_followup_report = await run_analyst_followup_stage(db_path, embedder, client)

    print_report(retrieval_report, toolcall_report, e2e_report, isolate_report, front_report, followup_report, analyst_followup_report)

    if args.json:
        reports = {
            name: report
            for name, report in [
                ("retrieval", retrieval_report),
                ("toolcall", toolcall_report),
                ("e2e", e2e_report),
                ("isolate", isolate_report),
                ("front", front_report),
                ("followup", followup_report),
                ("analyst_followup", analyst_followup_report),
            ]
            if report is not None
        }
        payload = build_json_payload(args.stage, reports, git_sha=_git_sha())
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str) + "\n")
        print(f"\nwrote {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        choices=["retrieval", "toolcall", "e2e", "isolate", "front", "followup", "analyst_followup", "all"],
        default="all",
    )
    parser.add_argument(
        "--json",
        metavar="PATH",
        default=None,
        help="also write stage reports + model config as JSON (for sweeps)",
    )
    args = parser.parse_args()
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
