"""Offline service builders: the cascade LLM, plus offline mode dispatch.

Cascade holds the four records tools on LOCAT_RAG_LLM_MODEL directly, with
reasoning off (qwen3 <think> must not reach TTS).
"""

from __future__ import annotations

import inspect

import pytest

import services


def test_cascade_llm_uses_rag_model_with_reasoning_off(monkeypatch):
    monkeypatch.setenv("LOCAT_RAG_LLM_MODEL", "qwen3.8:27b")
    llm = services.build_cascade_llm()
    assert llm._settings.model == "qwen3.8:27b"
    assert llm._settings.extra["extra_body"]["reasoning_effort"] == "none"


# --- mode dispatch: offline side ------------------------------------------


def _boom(*_args, **_kwargs):
    raise AssertionError("dispatch called the wrong builder")


def test_stt_for_offline_uses_local_builder(monkeypatch):
    """stt_for("offline") routes to build_stt, never the Deepgram one."""
    monkeypatch.setattr(services, "build_stt", lambda: "whisper-stt")
    monkeypatch.setattr(services, "build_online_stt", _boom)
    assert services.stt_for("offline") == "whisper-stt"


def test_tts_for_offline_uses_local_builder(monkeypatch):
    """tts_for("offline") routes to build_tts, never the Cartesia one."""
    monkeypatch.setattr(services, "build_tts", lambda: "kokoro-tts")
    monkeypatch.setattr(services, "build_online_tts", _boom)
    assert services.tts_for("offline") == "kokoro-tts"


@pytest.mark.parametrize("dispatch,builder", [("stt_for", "build_stt"), ("tts_for", "build_tts")])
def test_offline_dispatch_needs_no_cloud_keys(dispatch, builder, monkeypatch):
    """Offline dispatch never demands an online key, so an airgapped box works."""
    for name in ("DEEPGRAM_API_KEY", "OPENAI_API_KEY", "CARTESIA_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(services, "online_key", _boom)
    monkeypatch.setattr(services, builder, lambda: "local")
    assert getattr(services, dispatch)("offline") == "local"


@pytest.mark.parametrize("dispatch", ["stt_for", "tts_for"])
def test_dispatch_keeps_imports_lazy(dispatch):
    """Dispatchers delegate; engine imports stay inside the builders."""
    source = inspect.getsource(getattr(services, dispatch))
    assert "import" not in source
