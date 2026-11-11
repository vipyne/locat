"""Offline tests for scripts/prefetch_models.py — the pure tag-list logic.

Network paths (ollama pull subprocess, HF snapshot downloads) are exercised by
the real one-time warm-up run, not here.
"""

import json
import re
import subprocess

import pytest

import config
from scripts import prefetch_models as prefetch
from scripts.prefetch_models import (
    DAILY_BUNDLE_NAMES,
    _with_latest,
    daily_bundle_path,
    daily_bundle_url,
    daily_js_version,
    ollama_tags,
    prefetch_ollama_models,
    served_tags,
    tags_to_pull,
)


def test_ollama_tags_lists_llm_embed_then_analyst(monkeypatch):
    monkeypatch.setenv("LOCAT_LLM_MODEL", "qwen2.5:14b")
    monkeypatch.setenv("LOCAT_EMBED_MODEL", "nomic-embed-text")
    monkeypatch.setenv("LOCAT_RAG_LLM_MODEL", "qwen3:8b")

    assert ollama_tags() == ["qwen2.5:14b", "nomic-embed-text", "qwen3:8b"]


def test_ollama_tags_dedupes_identical_tags(monkeypatch):
    monkeypatch.setenv("LOCAT_LLM_MODEL", "same:1b")
    monkeypatch.setenv("LOCAT_EMBED_MODEL", "same:1b")
    monkeypatch.setenv("LOCAT_RAG_LLM_MODEL", "same:1b")

    assert ollama_tags() == ["same:1b"]


# --- Daily call-machine bundle (airgapped browser sessions) -------------------

LOCK = """{
  "name": "locat-client",
  "packages": {
    "": {"name": "locat-client"},
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
    lock.write_text('{"packages": {"": {"name": "locat-client"}}}')

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


# --- Already-pulled tags are not pulled again ---------------------------------


def test_with_latest_fills_in_the_implicit_tag():
    assert _with_latest("nomic-embed-text") == "nomic-embed-text:latest"
    assert _with_latest("qwen3.8:27b") == "qwen3.8:27b"


def test_tags_to_pull_drops_tags_the_store_already_has():
    wanted = ["qwen2.5:14b", "qwen3.8:27b", "qwen3:8b"]

    assert tags_to_pull(wanted, {"qwen3.8:27b"}) == ["qwen2.5:14b", "qwen3:8b"]


def test_tags_to_pull_matches_a_bare_name_against_latest():
    assert tags_to_pull(["nomic-embed-text"], {"nomic-embed-text:latest"}) == []


def test_tags_to_pull_without_a_served_set_pulls_everything():
    assert tags_to_pull(["a:1b", "b:2b"], set()) == ["a:1b", "b:2b"]


def _fake_tags_response(payload):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(payload).encode()

    return lambda url, timeout=None: _Resp()


def test_served_tags_reads_the_api_and_normalizes(monkeypatch):
    payload = {"models": [{"name": "qwen3.8:27b"}, {"name": "nomic-embed-text"}]}
    monkeypatch.setattr(prefetch.urllib.request, "urlopen", _fake_tags_response(payload))

    assert served_tags() == {"qwen3.8:27b", "nomic-embed-text:latest"}


def test_served_tags_is_empty_when_no_server_answers(monkeypatch):
    def _refuse(url, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(prefetch.urllib.request, "urlopen", _refuse)

    assert served_tags() == set()


@pytest.fixture()
def pull_recorder(monkeypatch):
    """Record every 'ollama pull' prefetch runs, with no server and no network."""
    pulled: list[str] = []

    def _run(cmd, **kwargs):
        if cmd[:2] == ["ollama", "pull"]:
            pulled.append(cmd[2])
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(prefetch.shutil, "which", lambda _: "/usr/local/bin/ollama")
    monkeypatch.setattr(prefetch, "_ollama_server_up", lambda: True)
    monkeypatch.setattr(prefetch.subprocess, "run", _run)
    monkeypatch.setattr(prefetch.subprocess, "Popen", _never_started)
    return pulled


def _never_started(*args, **kwargs):
    raise AssertionError("prefetch started a server when one was already up")


def test_prefetch_pulls_only_the_missing_tags(monkeypatch, pull_recorder):
    monkeypatch.setattr(prefetch, "ollama_tags", lambda: ["have:1b", "want:2b"])
    monkeypatch.setattr(prefetch, "served_tags", lambda: {"have:1b"})

    prefetch_ollama_models()

    assert pull_recorder == ["want:2b"]


def test_prefetch_pulls_nothing_when_the_store_is_warm(monkeypatch, pull_recorder):
    monkeypatch.setattr(prefetch, "ollama_tags", lambda: ["have:1b", "also"])
    monkeypatch.setattr(prefetch, "served_tags", lambda: {"have:1b", "also:latest"})

    prefetch_ollama_models()

    assert pull_recorder == []


def test_prefetch_force_repulls_every_tag(monkeypatch, pull_recorder):
    monkeypatch.setattr(prefetch, "ollama_tags", lambda: ["have:1b", "want:2b"])
    monkeypatch.setattr(prefetch, "served_tags", _never_asked)

    prefetch_ollama_models(force=True)

    assert pull_recorder == ["have:1b", "want:2b"]


def _never_asked():
    raise AssertionError("--force still probed the store")
