"""Offline tests for scripts/prefetch_models.py — the pure tag-list logic.

Network paths (ollama pull subprocess, HF snapshot downloads) are exercised by
the real one-time warm-up run, not here.
"""

import re

import pytest

import config
from scripts.prefetch_models import (
    DAILY_BUNDLE_NAMES,
    daily_bundle_path,
    daily_bundle_url,
    daily_js_version,
    ollama_tags,
)


def test_ollama_tags_lists_llm_embed_then_analyst(monkeypatch):
    monkeypatch.setenv("LOFI_LLM_MODEL", "qwen2.5:14b")
    monkeypatch.setenv("LOFI_EMBED_MODEL", "nomic-embed-text")
    monkeypatch.setenv("LOFI_RAG_LLM_MODEL", "qwen3:8b")

    assert ollama_tags() == ["qwen2.5:14b", "nomic-embed-text", "qwen3:8b"]


def test_ollama_tags_dedupes_identical_tags(monkeypatch):
    monkeypatch.setenv("LOFI_LLM_MODEL", "same:1b")
    monkeypatch.setenv("LOFI_EMBED_MODEL", "same:1b")
    monkeypatch.setenv("LOFI_RAG_LLM_MODEL", "same:1b")

    assert ollama_tags() == ["same:1b"]


# --- Daily call-machine bundle (airgapped browser sessions) -------------------

LOCK = """{
  "name": "lofi-client",
  "packages": {
    "": {"name": "lofi-client"},
    "node_modules/@daily-co/daily-js": {
      "version": "0.90.0",
      "resolved": "https://registry.npmjs.org/@daily-co/daily-js/-/daily-js-0.90.0.tgz"
    }
  }
}
"""


def test_daily_js_version_read_from_lock_file(tmp_path):
    lock = tmp_path / "package-lock.json"
    lock.write_text(LOCK)

    assert daily_js_version(lock) == "0.90.0"


def test_daily_js_version_without_entry_exits(tmp_path):
    lock = tmp_path / "package-lock.json"
    lock.write_text('{"packages": {"": {"name": "lofi-client"}}}')

    with pytest.raises(SystemExit):
        daily_js_version(lock)


def test_daily_js_version_of_the_repos_lock_is_a_release():
    assert re.fullmatch(r"\d+\.\d+\.\d+", daily_js_version())


@pytest.mark.parametrize("name", DAILY_BUNDLE_NAMES)
def test_daily_bundle_url_is_versioned_static_path(name):
    version = daily_js_version()

    assert daily_bundle_url(name) == (
        f"https://c.daily.co/call-machine/versioned/{version}/static/{name}"
    )


@pytest.mark.parametrize("name", DAILY_BUNDLE_NAMES)
def test_daily_bundle_path_sits_under_the_model_dir(monkeypatch, tmp_path, name):
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path)

    assert daily_bundle_path(name) == tmp_path / "daily" / name


def test_daily_bundles_are_the_call_machine_and_its_audio_processor():
    assert DAILY_BUNDLE_NAMES == (
        "call-machine-object-bundle.js",
        "audio-processor-bundle.js",
    )
