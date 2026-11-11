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
    """The three online model knobs have defaults and honor LOCAT_* overrides."""
    for name in ("LOCAT_ONLINE_LLM_MODEL", "LOCAT_ONLINE_STT_MODEL", "LOCAT_ONLINE_TTS_VOICE"):
        monkeypatch.delenv(name, raising=False)
    assert config.online_llm_model() == "gpt-4o-mini"
    assert config.online_stt_model() == "nova-3"
    assert config.online_tts_voice() == "86e30c1d-714b-4074-a1f2-1cb6b552fb49"

    monkeypatch.setenv("LOCAT_ONLINE_LLM_MODEL", "gpt-4.1-mini")
    assert config.online_llm_model() == "gpt-4.1-mini"


# --- mode dispatch: online side -------------------------------------------


def _boom(*_args, **_kwargs):
    raise AssertionError("dispatch called the wrong builder")


def test_stt_for_online_uses_deepgram_builder(monkeypatch):
    """stt_for("online") routes to build_online_stt, never the local one."""
    monkeypatch.setattr(services, "build_online_stt", lambda: "deepgram-stt")
    monkeypatch.setattr(services, "build_stt", _boom)
    assert services.stt_for("online") == "deepgram-stt"


def test_tts_for_online_uses_cartesia_builder(monkeypatch):
    """tts_for("online") routes to build_online_tts, never the local one."""
    monkeypatch.setattr(services, "build_online_tts", lambda: "cartesia-tts")
    monkeypatch.setattr(services, "build_tts", _boom)
    assert services.tts_for("online") == "cartesia-tts"


def test_online_dispatch_builds_real_cloud_services(fake_keys, no_ollama):
    """The online branches construct the real Deepgram/Cartesia services."""
    from pipecat.services.cartesia.tts import CartesiaTTSService
    from pipecat.services.deepgram.stt import DeepgramSTTService

    assert isinstance(services.stt_for("online"), DeepgramSTTService)
    assert isinstance(services.tts_for("online"), CartesiaTTSService)


@pytest.mark.parametrize("dispatch", ["stt_for", "tts_for"])
def test_dispatch_rejects_unknown_mode(dispatch):
    """An unknown mode exits naming the bad value and the two valid ones."""
    with pytest.raises(SystemExit) as excinfo:
        getattr(services, dispatch)("sideways")
    message = str(excinfo.value)
    assert "sideways" in message
    assert "offline" in message and "online" in message
