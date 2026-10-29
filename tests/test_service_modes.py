"""Per-service offline/online pins: LOCAT_STT_MODE / LOCAT_LLM_MODE /
LOCAT_TTS_MODE, and the per-service split of the online-key check.

Every test monkeypatches the three online keys — the repo's real `.env` is
dotenv-loaded when `config` imports, so an unpatched test would read live
keys (never their values; only presence is ever asserted).
"""

import pytest

import config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in config.ONLINE_KEY_NAMES:
        monkeypatch.setenv(name, f"fake-{name.lower()}")
    for name in ("LOCAT_STT_MODE", "LOCAT_LLM_MODE", "LOCAT_TTS_MODE"):
        monkeypatch.delenv(name, raising=False)


GETTERS = (
    ("stt_mode", "LOCAT_STT_MODE"),
    ("llm_mode", "LOCAT_LLM_MODE"),
    ("tts_mode", "LOCAT_TTS_MODE"),
)


@pytest.mark.parametrize("getter,var", GETTERS)
def test_default_is_session(getter, var):
    assert getattr(config, getter)() == "session"


@pytest.mark.parametrize("getter,var", GETTERS)
@pytest.mark.parametrize("value", ("offline", "online", "session"))
def test_reads_env_var(monkeypatch, getter, var, value):
    monkeypatch.setenv(var, value)
    assert getattr(config, getter)() == value


@pytest.mark.parametrize("getter,var", GETTERS)
def test_value_is_normalized(monkeypatch, getter, var):
    monkeypatch.setenv(var, "  OnLine  ")
    assert getattr(config, getter)() == "online"


@pytest.mark.parametrize("getter,var", GETTERS)
def test_invalid_value_exits_naming_the_var(monkeypatch, getter, var):
    monkeypatch.setenv(var, "local")
    with pytest.raises(SystemExit) as excinfo:
        getattr(config, getter)()
    message = str(excinfo.value)
    assert var in message
    assert "local" in message
    assert "offline" in message and "online" in message and "session" in message


@pytest.mark.parametrize("getter,var", GETTERS)
def test_blank_value_is_session(monkeypatch, getter, var):
    monkeypatch.setenv(var, "")
    assert getattr(config, getter)() == "session"


def test_getters_are_independent(monkeypatch):
    monkeypatch.setenv("LOCAT_LLM_MODE", "online")
    assert config.llm_mode() == "online"
    assert config.stt_mode() == "session"
    assert config.tts_mode() == "session"


def test_online_keys_by_service_covers_every_service():
    assert set(config.ONLINE_KEYS_BY_SERVICE) == {"stt", "llm", "tts"}
    assert config.ONLINE_KEYS_BY_SERVICE["stt"] == ("DEEPGRAM_API_KEY",)
    assert config.ONLINE_KEYS_BY_SERVICE["llm"] == ("OPENAI_API_KEY",)
    assert config.ONLINE_KEYS_BY_SERVICE["tts"] == ("CARTESIA_API_KEY",)


def test_missing_keys_for_llm_only(monkeypatch):
    for name in config.ONLINE_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    assert config.missing_online_keys(services=("llm",)) == ["OPENAI_API_KEY"]


def test_missing_keys_for_subset_keeps_canonical_order(monkeypatch):
    for name in config.ONLINE_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    assert config.missing_online_keys(services=("tts", "llm")) == [
        "OPENAI_API_KEY",
        "CARTESIA_API_KEY",
    ]


def test_missing_keys_no_arg_checks_all_three(monkeypatch):
    for name in config.ONLINE_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    assert config.missing_online_keys() == list(config.ONLINE_KEY_NAMES)


def test_missing_keys_skips_present_keys(monkeypatch):
    monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
    assert config.missing_online_keys(services=("llm", "tts")) == ["CARTESIA_API_KEY"]
    assert config.missing_online_keys(services=("llm",)) == []


def test_missing_keys_empty_services_checks_nothing(monkeypatch):
    for name in config.ONLINE_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    assert config.missing_online_keys(services=()) == []


def test_missing_keys_unknown_service_exits(monkeypatch):
    with pytest.raises(SystemExit):
        config.missing_online_keys(services=("brain",))
