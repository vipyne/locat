"""Shared analyst text helpers: thinking-tag stripping and the grounded
question preamble (today's date, records date range, category list) that keeps
the analyst system prompt byte-stable for the KV cache."""

from __future__ import annotations

import datetime
import re

import store
from tunables import (
    GROUNDING_CATEGORIES,
    GROUNDING_DATE_RANGE,
    GROUNDING_TODAY,
    QUESTION_PREFIX,
)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_think(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


def grounded_question(question: str, db_path, today: datetime.date | None = None) -> str:
    """Question as the analyst sees it: today's date, the records' date range,
    and the category list prepended, keeping the system prompt byte-stable."""
    today = today or datetime.date.today()
    parts = [GROUNDING_TODAY.format(today=today.isoformat())]
    span = store.date_range(db_path)
    if span:
        parts.append(GROUNDING_DATE_RANGE.format(start=span[0], end=span[1]))
    names = store.categories(db_path)
    if names:
        parts.append(GROUNDING_CATEGORIES.format(categories=", ".join(names)))
    return " ".join(parts) + QUESTION_PREFIX + question
