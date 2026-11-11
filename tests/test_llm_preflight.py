"""Tests for which models the LLM preflight checks against Ollama: the
architecture's conversation model(s)."""

import pytest

import bot
import config
from modes import resolve_modes


@pytest.fixture(autouse=True)
def clean_pins(monkeypatch):
    for name in config.ONLINE_KEY_NAMES:
        monkeypatch.setenv(name, f"fake-{name.lower()}")
    for name in ("LOCAT_STT_MODE", "LOCAT_LLM_MODE", "LOCAT_TTS_MODE"):
        monkeypatch.delenv(name, raising=False)


def test_models_to_check_per_architecture(monkeypatch):
    monkeypatch.setenv("LOCAT_LLM_MODEL", "qwen2.5:7b")
    monkeypatch.setenv("LOCAT_RAG_LLM_MODEL", "qwen3.8:27b")
    monkeypatch.setenv("LOCAT_ARCHITECTURE", "cascade-simple-chat")
    assert bot._ollama_models_to_check() == ["qwen2.5:7b"]
    monkeypatch.setenv("LOCAT_ARCHITECTURE", "cascade")
    assert bot._ollama_models_to_check() == ["qwen3.8:27b"]
    monkeypatch.setenv("LOCAT_ARCHITECTURE", "llmwithbackend")
    assert bot._ollama_models_to_check() == ["qwen2.5:7b", "qwen3.8:27b"]


def test_split_same_model_checked_once(monkeypatch):
    monkeypatch.setenv("LOCAT_ARCHITECTURE", "llmwithbackend")
    monkeypatch.setenv("LOCAT_LLM_MODEL", "front:7b")
    monkeypatch.setenv("LOCAT_RAG_LLM_MODEL", "front:7b")
    assert bot._ollama_models_to_check() == ["front:7b"]


def test_llm_pinned_online_skips_llm_and_rag_preflight(monkeypatch):
    ran = []
    monkeypatch.setattr(bot, "_preflight_llm", lambda url: ran.append("llm"))
    monkeypatch.setattr(bot, "_preflight_rag", lambda: ran.append("rag"))
    monkeypatch.setattr(bot, "_preflight_stt", lambda: ran.append("stt"))
    monkeypatch.setenv("LOCAT_LLM_MODE", "online")
    bot._run_local_preflights(resolve_modes("offline"))
    assert ran == ["stt"]


def test_llm_session_default_runs_llm_and_rag_preflight(monkeypatch):
    ran = []
    monkeypatch.setattr(bot, "_preflight_llm", lambda url: ran.append("llm"))
    monkeypatch.setattr(bot, "_preflight_rag", lambda: ran.append("rag"))
    monkeypatch.setattr(bot, "_preflight_stt", lambda: ran.append("stt"))
    bot._run_local_preflights(resolve_modes("offline"))
    assert ran == ["llm", "rag", "stt"]
