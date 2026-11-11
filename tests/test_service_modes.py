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


# --- modes.resolve_modes / validate_modes -------------------------------------


@pytest.fixture(autouse=True)
def clean_serve_flags(monkeypatch):
    for name in ("LOCAT_ONLINE_ONLY", "LOCAT_OFFLINE_ONLY", "LOCAT_ARCHITECTURE"):
        monkeypatch.delenv(name, raising=False)


def test_session_modes_is_a_frozen_dataclass():
    import dataclasses

    import modes

    assert dataclasses.is_dataclass(modes.SessionModes)
    fields = [f.name for f in dataclasses.fields(modes.SessionModes)]
    assert fields == ["session", "stt", "llm", "tts"]
    built = modes.SessionModes(session="offline", stt="offline", llm="offline", tts="offline")
    with pytest.raises(dataclasses.FrozenInstanceError):
        built.llm = "online"


def test_mode_constants_live_in_modes():
    import modes

    assert modes.OFFLINE_MODE == "offline"
    assert modes.ONLINE_MODE == "online"


def test_pipeline_still_re_exports_the_constants():
    """`from pipeline import OFFLINE_MODE` keeps working, modes.py owns them."""
    import inspect

    import pipeline

    from modes import OFFLINE_MODE, ONLINE_MODE

    assert pipeline.OFFLINE_MODE is OFFLINE_MODE
    assert pipeline.ONLINE_MODE is ONLINE_MODE
    source = inspect.getsource(pipeline)
    assert 'ONLINE_MODE = "online"' not in source
    assert 'OFFLINE_MODE = "offline"' not in source


@pytest.mark.parametrize("session", ("offline", "online"))
def test_unpinned_services_follow_the_session(session):
    import modes

    resolved = modes.resolve_modes(session)
    assert (resolved.session, resolved.stt, resolved.llm, resolved.tts) == (
        session,
        session,
        session,
        session,
    )


@pytest.mark.parametrize("session", ("offline", "online"))
@pytest.mark.parametrize("service,var", (("stt", "LOCAT_STT_MODE"), ("llm", "LOCAT_LLM_MODE"), ("tts", "LOCAT_TTS_MODE")))
@pytest.mark.parametrize("pin", ("offline", "online"))
def test_a_pin_beats_the_session(monkeypatch, session, service, var, pin):
    import modes

    monkeypatch.setenv(var, pin)
    resolved = modes.resolve_modes(session)
    assert getattr(resolved, service) == pin
    for other in ("stt", "llm", "tts"):
        if other != service:
            assert getattr(resolved, other) == session


@pytest.mark.parametrize("session", ("offline", "online"))
def test_session_valued_pin_follows_the_session(monkeypatch, session):
    import modes

    monkeypatch.setenv("LOCAT_LLM_MODE", "session")
    assert modes.resolve_modes(session).llm == session


def test_unrecognized_session_falls_back_to_offline():
    import modes

    for raw in (None, "", "  ", "local", "both"):
        assert modes.resolve_modes(raw).session == "offline"
        assert modes.resolve_modes(raw).all_offline


def test_mixed_audio_offline_brain_online():
    import modes

    resolved = modes.SessionModes(session="offline", stt="offline", llm="online", tts="offline")
    assert resolved.any_online
    assert not resolved.all_offline
    assert resolved.online_services == ("llm",)
    assert resolved.offline_services == ("stt", "tts")


def test_all_offline_helpers():
    import modes

    resolved = modes.resolve_modes("offline")
    assert resolved.all_offline
    assert not resolved.any_online
    assert resolved.online_services == ()
    assert resolved.offline_services == ("stt", "llm", "tts")


def test_all_online_helpers():
    import modes

    resolved = modes.resolve_modes("online")
    assert resolved.any_online
    assert not resolved.all_offline
    assert resolved.online_services == ("stt", "llm", "tts")
    assert resolved.offline_services == ()


def test_simple_chat_forces_all_offline(monkeypatch):
    import modes

    monkeypatch.setenv("LOCAT_ARCHITECTURE", "cascade-simple-chat")
    monkeypatch.setenv("LOCAT_LLM_MODE", "online")
    monkeypatch.setenv("LOCAT_TTS_MODE", "online")
    resolved = modes.resolve_modes("online")
    assert resolved.all_offline
    assert resolved.session == "offline"
    modes.validate_modes(resolved)


def test_validate_accepts_all_offline():
    import modes

    modes.validate_modes(modes.resolve_modes("offline"))


def test_validate_accepts_all_online_with_keys():
    import modes

    modes.validate_modes(modes.resolve_modes("online"))


@pytest.mark.parametrize("service,var", (("stt", "LOCAT_STT_MODE"), ("llm", "LOCAT_LLM_MODE"), ("tts", "LOCAT_TTS_MODE")))
def test_online_only_refuses_an_offline_service(monkeypatch, service, var):
    import modes

    monkeypatch.setenv("LOCAT_ONLINE_ONLY", "1")
    monkeypatch.setenv(var, "offline")
    with pytest.raises(RuntimeError) as excinfo:
        modes.validate_modes(modes.resolve_modes("online"))
    message = str(excinfo.value)
    assert "--online-only" in message
    assert service in message


@pytest.mark.parametrize("service,var", (("stt", "LOCAT_STT_MODE"), ("llm", "LOCAT_LLM_MODE"), ("tts", "LOCAT_TTS_MODE")))
def test_offline_only_refuses_an_online_service(monkeypatch, service, var):
    import modes

    monkeypatch.setenv("LOCAT_OFFLINE_ONLY", "1")
    monkeypatch.setenv(var, "online")
    with pytest.raises(RuntimeError) as excinfo:
        modes.validate_modes(modes.resolve_modes("offline"))
    message = str(excinfo.value)
    assert "--offline-only" in message
    assert service in message


def test_offline_only_names_every_online_service_at_once(monkeypatch):
    import modes

    monkeypatch.setenv("LOCAT_OFFLINE_ONLY", "1")
    monkeypatch.setenv("LOCAT_STT_MODE", "online")
    monkeypatch.setenv("LOCAT_TTS_MODE", "online")
    with pytest.raises(RuntimeError) as excinfo:
        modes.validate_modes(modes.resolve_modes("offline"))
    message = str(excinfo.value)
    assert "stt" in message and "tts" in message


def test_online_only_names_every_offline_service_at_once(monkeypatch):
    import modes

    monkeypatch.setenv("LOCAT_ONLINE_ONLY", "1")
    monkeypatch.setenv("LOCAT_STT_MODE", "offline")
    monkeypatch.setenv("LOCAT_TTS_MODE", "offline")
    with pytest.raises(RuntimeError) as excinfo:
        modes.validate_modes(modes.resolve_modes("online"))
    message = str(excinfo.value)
    assert "stt" in message and "tts" in message


def test_validate_reports_only_keys_for_online_services(monkeypatch):
    import modes

    for name in config.ONLINE_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LOCAT_LLM_MODE", "online")
    with pytest.raises(RuntimeError) as excinfo:
        modes.validate_modes(modes.resolve_modes("offline"))
    message = str(excinfo.value)
    assert "OPENAI_API_KEY" in message
    assert "DEEPGRAM_API_KEY" not in message
    assert "CARTESIA_API_KEY" not in message


def test_validate_reports_all_missing_keys_in_one_message(monkeypatch):
    import modes

    for name in config.ONLINE_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        modes.validate_modes(modes.resolve_modes("online"))
    message = str(excinfo.value)
    for name in config.ONLINE_KEY_NAMES:
        assert name in message


def test_validate_ignores_missing_keys_for_an_all_offline_session(monkeypatch):
    import modes

    for name in config.ONLINE_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    modes.validate_modes(modes.resolve_modes("offline"))
