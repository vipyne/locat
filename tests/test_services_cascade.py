"""services.build_cascade_llm(): the offline cascade's tool-holding model.

Cascade holds the four records tools on LOFI_RAG_LLM_MODEL directly, with
reasoning off (qwen3 <think> must not reach TTS).
"""

from __future__ import annotations

import services


def test_cascade_llm_uses_rag_model_with_reasoning_off(monkeypatch):
    monkeypatch.setenv("LOFI_RAG_LLM_MODEL", "qwen3.8:27b")
    llm = services.build_cascade_llm()
    assert llm._settings.model == "qwen3.8:27b"
    assert llm._settings.extra["extra_body"]["reasoning_effort"] == "none"
