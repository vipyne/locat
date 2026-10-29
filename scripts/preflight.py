#!/usr/bin/env python
"""preflight.py — list what ``./lofi.sh start`` still lacks.

Prints one line per missing prerequisite, ``<kind> <detail>``, and nothing
when the machine is ready. lofi.sh reads the lines and points at the fix.

    extra <name>      an optional uv extra the configured engines need
    ollama <tag>      an Ollama model the offline stack loads
    stt / tts         the configured STT / TTS weights
    daily <file>      a vendored daily-js bundle (airgapped browser sessions)
    key <NAME>        an online-session API key missing from .env
    rag / rag-public  a document index that has documents but no build

Usage:
    uv run python scripts/preflight.py --mode both --transport browser
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import store  # noqa: E402
from prefetch_models import DAILY_BUNDLE_NAMES, daily_bundle_path  # noqa: E402
from print_models import _missing, _ollama_manifest, _stt_file, _tts_file  # noqa: E402

# extra name → the module it installs (same probes as configure.sh)
EXTRA_MODULES = {"moonshine": "moonshine_voice", "piper": "piper", "local-audio": "pyaudio"}


def _with_latest(tag: str) -> str:
    return tag if ":" in tag else f"{tag}:latest"


def _served_tags() -> set[str] | None:
    """Tags the running Ollama serves, or None when no server answers."""
    url = config.ollama_api_url().rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            models = json.load(resp).get("models", [])
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return {_with_latest(m.get("name", "")) for m in models}


def offline_tags() -> list[str]:
    tags = [config.llm_model(), config.embed_model()]
    # llmwithbackend's analyst and cascade's tool-holder both run LOFI_RAG_LLM_MODEL.
    if config.architecture() != "cascade-simple-chat":
        tags.append(config.rag_llm_model())
    return list(dict.fromkeys(tags))


def _has_documents(directory: str) -> bool:
    path = Path(directory)
    return path.is_dir() and any(p.is_file() for p in path.rglob("*"))


def missing_extras(transport: str, offline: bool) -> list[str]:
    needed = []
    if offline and config.stt_engine() == "moonshine":
        needed.append("moonshine")
    if offline and config.tts_engine() == "piper":
        needed.append("piper")
    if transport == "headphones":
        needed.append("local-audio")
    return [e for e in needed if importlib.util.find_spec(EXTRA_MODULES[e]) is None]


def missing_offline() -> list[str]:
    lines = []
    served = _served_tags()
    for tag in offline_tags():
        if served is not None:
            present = _with_latest(tag) in served
        else:
            present = _ollama_manifest(Path(config.ollama_models_dir()), tag) is not None
        if not present:
            lines.append(f"ollama {tag}")
    if _missing(_stt_file()):
        lines.append(f"stt {config.stt_engine()}")
    if _missing(_tts_file()):
        lines.append(f"tts {config.tts_engine()}")
    if not store.has_index(Path(config.rag_db_path())) and _has_documents(config.rag_data_dir()):
        lines.append(f"rag {config.rag_data_dir()}")
    return lines


def missing_online() -> list[str]:
    lines = [f"key {name}" for name in config.missing_online_keys()]
    if (
        os.getenv("OPENAI_API_KEY", "").strip()
        and not store.has_index(Path(config.public_rag_db_path()))
        and _has_documents(config.public_rag_data_dir())
    ):
        lines.append(f"rag-public {config.public_rag_data_dir()}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("both", "offline-only", "online-only"), default="both")
    parser.add_argument("--transport", choices=("browser", "headphones"), default="browser")
    args = parser.parse_args()

    offline = args.mode != "online-only"
    online = args.mode != "offline-only"
    lines = [f"extra {e}" for e in missing_extras(args.transport, offline)]
    if offline:
        lines += missing_offline()
    # Online sessions have the network: /daily/ falls back to the CDN.
    if offline and args.transport == "browser":
        lines += [f"daily {n}" for n in DAILY_BUNDLE_NAMES if not daily_bundle_path(n).is_file()]
    if online:
        lines += missing_online()
    for line in lines:
        print(line)


if __name__ == "__main__":
    main()
