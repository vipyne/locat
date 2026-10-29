"""Tests for which models the LLM preflight checks against Ollama: the
architecture's conversation model(s)."""

import bot


def test_models_to_check_per_architecture(monkeypatch):
    monkeypatch.setenv("LOFI_LLM_MODEL", "qwen2.5:7b")
    monkeypatch.setenv("LOFI_RAG_LLM_MODEL", "qwen3.8:27b")
    monkeypatch.setenv("LOFI_ARCHITECTURE", "cascade-simple-chat")
    assert bot._ollama_models_to_check() == ["qwen2.5:7b"]
    monkeypatch.setenv("LOFI_ARCHITECTURE", "cascade")
    assert bot._ollama_models_to_check() == ["qwen3.8:27b"]
    monkeypatch.setenv("LOFI_ARCHITECTURE", "llmwithbackend")
    assert bot._ollama_models_to_check() == ["qwen2.5:7b", "qwen3.8:27b"]


def test_split_same_model_checked_once(monkeypatch):
    monkeypatch.setenv("LOFI_ARCHITECTURE", "llmwithbackend")
    monkeypatch.setenv("LOFI_LLM_MODEL", "front:7b")
    monkeypatch.setenv("LOFI_RAG_LLM_MODEL", "front:7b")
    assert bot._ollama_models_to_check() == ["front:7b"]
