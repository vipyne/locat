"""Online-mode service builders: Deepgram STT, OpenAI LLM, Cartesia TTS.

Every test keeps the three keys fake and local: constructors must not reach
the network, and no online builder may touch Ollama.
"""

from __future__ import annotations

import pytest

import config
import services

ONLINE_KEYS = ("OPENAI_API_KEY", "DEEPGRAM_API_KEY", "CARTESIA_API_KEY")


@pytest.fixture
def fake_keys(monkeypatch):
    """All three online keys present, obviously fake."""
    for name in ONLINE_KEYS:
        monkeypatch.setenv(name, f"fake-{name.lower()}")


@pytest.fixture
def no_ollama(monkeypatch):
    """Any Ollama lookup from an online builder is a test failure."""

    def boom(*_args, **_kwargs):
        raise AssertionError("online builder touched Ollama")

    monkeypatch.setattr(config, "ollama_base_url", boom)
    monkeypatch.setattr(config, "ollama_api_url", boom)


def test_online_builders_construct_without_network(fake_keys, no_ollama):
    """The three cloud services instantiate offline, with the configured models."""
    stt = services.build_online_stt()
    llm = services.build_online_llm()
    tts = services.build_online_tts()

    from pipecat.services.cartesia.tts import CartesiaTTSService
    from pipecat.services.deepgram.stt import DeepgramSTTService
    from pipecat.services.openai.llm import OpenAILLMService

    assert isinstance(stt, DeepgramSTTService)
    assert isinstance(llm, OpenAILLMService)
    assert isinstance(tts, CartesiaTTSService)
    assert stt._settings.model == config.online_stt_model()
    assert llm._settings.model == config.online_llm_model()
    assert tts._settings.voice == config.online_tts_voice()


def test_online_llm_temperature_override(fake_keys, no_ollama):
    """A temperature override reaches the service's settings."""
    llm = services.build_online_llm(temperature=0)
    assert llm._settings.temperature == 0


@pytest.mark.parametrize(
    "key,builder",
    [
        ("DEEPGRAM_API_KEY", "build_online_stt"),
        ("OPENAI_API_KEY", "build_online_llm"),
        ("CARTESIA_API_KEY", "build_online_tts"),
    ],
)
def test_online_builder_missing_key_exits(key, builder, fake_keys, monkeypatch):
    """A builder whose key is blank exits naming that key."""
    monkeypatch.setenv(key, "")
    with pytest.raises(SystemExit) as excinfo:
        getattr(services, builder)()
    assert key in str(excinfo.value)


def test_missing_online_keys_lists_all(monkeypatch):
    """Every absent key is named, in the canonical order."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "fake-deepgram")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.delenv("CARTESIA_API_KEY", raising=False)

    assert config.missing_online_keys() == ["OPENAI_API_KEY", "CARTESIA_API_KEY"]


def test_missing_online_keys_empty_when_all_present(monkeypatch):
    for name in ONLINE_KEYS:
        monkeypatch.setenv(name, f"fake-{name.lower()}")
    assert config.missing_online_keys() == []


def test_online_model_defaults(monkeypatch):
    """The three online model knobs have defaults and honor LOFI_* overrides."""
    for name in ("LOFI_ONLINE_LLM_MODEL", "LOFI_ONLINE_STT_MODEL", "LOFI_ONLINE_TTS_VOICE"):
        monkeypatch.delenv(name, raising=False)
    assert config.online_llm_model() == "gpt-4o-mini"
    assert config.online_stt_model() == "nova-3"
    assert config.online_tts_voice() == "86e30c1d-714b-4074-a1f2-1cb6b552fb49"

    monkeypatch.setenv("LOFI_ONLINE_LLM_MODEL", "gpt-4.1-mini")
    assert config.online_llm_model() == "gpt-4.1-mini"
