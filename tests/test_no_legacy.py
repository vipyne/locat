"""Grep-style guard: no references to pre-rename/pre-adoption names remain.

Scans every git-tracked text file except this test itself and the `ralph/` /
`ralph-archive/` process directories (meta notes about past phases, not
product code or docs).
"""

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

EXCLUDED_PREFIXES = ("ralph/", "ralph-archive/")
EXCLUDED_FILES = {"tests/test_no_legacy.py"}

# (label, compiled pattern) — all substring matches, not anchored to word boundaries.
PATTERNS = [
    ("moq", re.compile(r"moq", re.IGNORECASE)),
    ("observer_processor", re.compile(r"observer_processor")),
    ("bot_moq", re.compile(r"bot_moq")),
    ("bot_web", re.compile(r"bot_web")),
    ("prompts/financial_advisor", re.compile(r"prompts/financial_advisor")),
    ("spike-vpio", re.compile(r"spike-vpio")),
    ("lofi", re.compile(r"lofi", re.IGNORECASE)),
]


def _tracked_files():
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    for rel in out.splitlines():
        if rel in EXCLUDED_FILES or rel.startswith(EXCLUDED_PREFIXES):
            continue
        yield rel


def test_no_legacy_references():
    hits = []
    for rel in _tracked_files():
        path = REPO_ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            continue
        for label, pattern in PATTERNS:
            if pattern.search(text):
                hits.append(f"{rel}: matches {label!r}")
    assert not hits, "legacy references found:\n" + "\n".join(hits)
