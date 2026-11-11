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
        except (FileNotFoundError, UnicodeDecodeError, ValueError):
            continue
        for label, pattern in PATTERNS:
            if pattern.search(text):
                hits.append(f"{rel}: matches {label!r}")
    assert not hits, "legacy references found:\n" + "\n".join(hits)


# Doc files that document LOCAT_* knobs for humans; every var named here must
# have a reader somewhere in the actual source.
ENV_DOC_FILES = ("env.example", "locatrc.example", "README.md")
LOCAT_VAR_RE = re.compile(r"LOCAT_[A-Z0-9_]+")
SOURCE_SUFFIXES = (".py", ".sh", ".ts")


def _documented_locat_vars():
    found = set()
    for rel in ENV_DOC_FILES:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        found.update(LOCAT_VAR_RE.findall(text))
    return found


def _source_text_by_file():
    texts = {}
    for rel in _tracked_files():
        if rel in ENV_DOC_FILES:
            continue
        if not rel.endswith(SOURCE_SUFFIXES):
            continue
        path = REPO_ROOT / rel
        try:
            texts[rel] = path.read_text(encoding="utf-8")
        except (FileNotFoundError, UnicodeDecodeError, ValueError):
            continue
    return texts


def test_no_dead_locat_env_vars():
    sources = _source_text_by_file()
    combined = "\n".join(sources.values())
    dead = sorted(
        var for var in _documented_locat_vars() if var not in combined
    )
    assert not dead, "LOCAT_* vars documented but never read by code:\n" + "\n".join(dead)


# Phrases that promise behavior the per-service mode pins (LOCAT_STT_MODE /
# LOCAT_LLM_MODE / LOCAT_TTS_MODE) can make false — configure.sh only sizes
# offline model choices for the machine, so it must not also claim the app
# is offline-only / cloud-free.
CONFIGURE_SH_CONTRADICTIONS = [
    "no cloud",
    "fully offline",
    "never leaves your machine",
    "no internet required",
    "entirely offline",
    "entirely local",
]


def test_configure_sh_does_not_contradict_per_service_modes():
    text = (REPO_ROOT / "configure.sh").read_text(encoding="utf-8").lower()
    hits = [phrase for phrase in CONFIGURE_SH_CONTRADICTIONS if phrase in text]
    assert not hits, "configure.sh makes offline-only claims per-service modes can break:\n" + "\n".join(hits)
