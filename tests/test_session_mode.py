"""Session mode plumbing: which mode a connect request selects, and the
fail-fast checks guarding each mode.

Every test monkeypatches the three online keys — the repo's real `.env` is
dotenv-loaded when `config` imports, so an unpatched test would read live
keys (never their values; only presence is ever asserted).
"""

import pytest

import bot_browser
import config


@pytest.fixture(autouse=True)
def fake_keys(monkeypatch):
    for name in config.ONLINE_KEY_NAMES:
        monkeypatch.setenv(name, f"fake-{name.lower()}")
    monkeypatch.delenv("LOFI_ONLINE_ONLY", raising=False)
    monkeypatch.delenv("LOFI_OFFLINE_ONLY", raising=False)


class FakeWebSocket:
    """Stands in for the starlette WebSocket the runner hands /ws-client."""

    def __init__(self, **query):
        self.query_params = dict(query)


def ws_args(**query):
    from pipecat.runner.types import WebSocketRunnerArguments

    return WebSocketRunnerArguments(
        websocket=FakeWebSocket(**query), transport_type="websocket"
    )


def webrtc_args(body=None):
    from pipecat.runner.types import SmallWebRTCRunnerArguments

    return SmallWebRTCRunnerArguments(webrtc_connection=object(), body=body)


def test_session_mode_from_query():
    assert bot_browser.session_mode(ws_args(mode="online")) == "online"
    assert bot_browser.session_mode(ws_args(mode="offline")) == "offline"
    assert bot_browser.session_mode(ws_args()) == "offline"
    assert bot_browser.session_mode(ws_args(mode="ONLINE")) == "online"
    assert bot_browser.session_mode(ws_args(mode="banana")) == "offline"


def test_session_mode_from_webrtc_request_data():
    assert bot_browser.session_mode(webrtc_args({"mode": "online"})) == "online"
    assert bot_browser.session_mode(webrtc_args({"mode": "offline"})) == "offline"
    assert bot_browser.session_mode(webrtc_args({})) == "offline"
    assert bot_browser.session_mode(webrtc_args(None)) == "offline"
    assert bot_browser.session_mode(webrtc_args("not-a-dict")) == "offline"


def test_online_connect_missing_keys_lists_all(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "")
    monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        bot_browser.session_mode(ws_args(mode="online"))
    message = str(excinfo.value)
    assert "DEEPGRAM_API_KEY" in message
    assert "CARTESIA_API_KEY" in message
    assert "OPENAI_API_KEY" not in message


def test_online_connect_with_all_keys_is_allowed():
    assert bot_browser.session_mode(ws_args(mode="online")) == "online"


def test_online_only_rejects_offline_connect(monkeypatch):
    monkeypatch.setenv("LOFI_ONLINE_ONLY", "1")
    with pytest.raises(RuntimeError) as excinfo:
        bot_browser.session_mode(ws_args(mode="offline"))
    assert "--online-only" in str(excinfo.value)


def test_online_only_defaults_a_bare_connect_to_online(monkeypatch):
    monkeypatch.setenv("LOFI_ONLINE_ONLY", "1")
    assert bot_browser.session_mode(ws_args()) == "online"


def test_online_only_flag_parsing(monkeypatch):
    assert config.online_only() is False
    for raw in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("LOFI_ONLINE_ONLY", raw)
        assert config.online_only() is True, raw
    for raw in ("", "0", "false", "no"):
        monkeypatch.setenv("LOFI_ONLINE_ONLY", raw)
        assert config.online_only() is False, raw


def test_offline_only_rejects_online_connect(monkeypatch):
    monkeypatch.setenv("LOFI_OFFLINE_ONLY", "1")
    with pytest.raises(RuntimeError) as excinfo:
        bot_browser.session_mode(ws_args(mode="online"))
    assert "--offline-only" in str(excinfo.value)


def test_offline_only_bare_connect_stays_offline(monkeypatch):
    monkeypatch.setenv("LOFI_OFFLINE_ONLY", "1")
    assert bot_browser.session_mode(ws_args()) == "offline"


def test_conflicting_config_serves_offline(monkeypatch):
    monkeypatch.setenv("LOFI_ONLINE_ONLY", "1")
    monkeypatch.setenv("LOFI_OFFLINE_ONLY", "1")
    assert bot_browser.session_mode(ws_args()) == "offline"
    assert bot_browser.session_mode(ws_args(mode="offline")) == "offline"
    with pytest.raises(RuntimeError):
        bot_browser.session_mode(ws_args(mode="online"))


def test_offline_only_still_runs_local_preflights(monkeypatch):
    ran = []
    monkeypatch.setattr(bot_browser, "_preflight_llm", lambda url: ran.append("llm"))
    monkeypatch.setattr(bot_browser, "_preflight_stt", lambda: ran.append("stt"))
    monkeypatch.setattr(bot_browser, "_preflight_rag", lambda: ran.append("rag"))
    monkeypatch.setenv("LOFI_OFFLINE_ONLY", "1")
    bot_browser._preflight_local()
    assert ran == ["llm", "stt", "rag"]


def test_offline_only_flag_parsing(monkeypatch):
    assert config.offline_only() is False
    for raw in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("LOFI_OFFLINE_ONLY", raw)
        assert config.offline_only() is True, raw
    for raw in ("", "0", "false", "no"):
        monkeypatch.setenv("LOFI_OFFLINE_ONLY", raw)
        assert config.offline_only() is False, raw


def test_offline_only_wins_a_conflict(monkeypatch):
    monkeypatch.setenv("LOFI_ONLINE_ONLY", "1")
    monkeypatch.setenv("LOFI_OFFLINE_ONLY", "1")
    assert config.offline_only() is True
    assert config.online_only() is False


def test_both_zero_means_both_modes(monkeypatch):
    monkeypatch.setenv("LOFI_ONLINE_ONLY", "0")
    monkeypatch.setenv("LOFI_OFFLINE_ONLY", "0")
    assert config.serve_mode() == "both"


def test_serve_mode_names_each_restriction(monkeypatch):
    assert config.serve_mode() == "both"
    monkeypatch.setenv("LOFI_ONLINE_ONLY", "1")
    assert config.serve_mode() == "online-only"
    monkeypatch.setenv("LOFI_OFFLINE_ONLY", "1")
    assert config.serve_mode() == "offline-only"
    monkeypatch.delenv("LOFI_ONLINE_ONLY")
    assert config.serve_mode() == "offline-only"


def test_online_only_skips_local_preflights(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("local preflight ran in an online-only process")

    monkeypatch.setattr(bot_browser, "_preflight_llm", boom)
    monkeypatch.setattr(bot_browser, "_preflight_stt", boom)
    monkeypatch.setattr(bot_browser, "_preflight_rag", boom)
    monkeypatch.setenv("LOFI_ONLINE_ONLY", "1")
    bot_browser._preflight_local()


def test_local_preflights_run_by_default(monkeypatch):
    ran = []
    monkeypatch.setattr(bot_browser, "_preflight_llm", lambda url: ran.append(("llm", url)))
    monkeypatch.setattr(bot_browser, "_preflight_stt", lambda: ran.append(("stt",)))
    monkeypatch.setattr(bot_browser, "_preflight_rag", lambda: ran.append(("rag",)))
    bot_browser._preflight_local()
    assert [entry[0] for entry in ran] == ["llm", "stt", "rag"]


@pytest.fixture
def http_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    bot_browser._serve_client_dist(app)
    return TestClient(app)


def test_lofi_mode_endpoint_reports_both(http_client):
    resp = http_client.get("/api/lofi-mode")
    assert resp.status_code == 200
    assert resp.json() == {"mode": "both"}


def test_lofi_mode_endpoint_reports_online_only(http_client, monkeypatch):
    monkeypatch.setenv("LOFI_ONLINE_ONLY", "1")
    assert http_client.get("/api/lofi-mode").json() == {"mode": "online-only"}


def test_lofi_mode_endpoint_reports_offline_only(http_client, monkeypatch):
    monkeypatch.setenv("LOFI_OFFLINE_ONLY", "1")
    assert http_client.get("/api/lofi-mode").json() == {"mode": "offline-only"}


def test_offline_connect_missing_models_lists_all(monkeypatch):
    monkeypatch.setenv("LOFI_ARCHITECTURE", "llmwithbackend")
    monkeypatch.setenv("LOFI_LLM_MODEL", "front-model:8b")
    monkeypatch.setenv("LOFI_RAG_LLM_MODEL", "analyst-model:27b")
    monkeypatch.setattr(bot_browser, "_ollama_model_ids", lambda url: {"someone-else:1b"})
    with pytest.raises(RuntimeError) as excinfo:
        bot_browser.vet_offline_session()
    message = str(excinfo.value)
    assert "front-model:8b" in message
    assert "analyst-model:27b" in message
    assert "scripts/prefetch_models.py" in message
    assert "scripts/run_ollama.sh" in message


def test_offline_connect_with_every_model_pulled_is_allowed(monkeypatch):
    monkeypatch.setenv("LOFI_ARCHITECTURE", "llmwithbackend")
    monkeypatch.setenv("LOFI_LLM_MODEL", "front-model:8b")
    monkeypatch.setenv("LOFI_RAG_LLM_MODEL", "analyst-model:27b")
    monkeypatch.setattr(
        bot_browser,
        "_ollama_model_ids",
        lambda url: {"front-model:latest", "analyst-model:27b"},
    )
    bot_browser.vet_offline_session()


def test_offline_connect_ollama_down_names_fix(monkeypatch):
    def refused(url):
        raise OSError("Connection refused")

    monkeypatch.setattr(bot_browser, "_ollama_model_ids", refused)
    with pytest.raises(RuntimeError) as excinfo:
        bot_browser.vet_offline_session()
    message = str(excinfo.value)
    assert "scripts/run_ollama.sh" in message
    assert "Connection refused" in message


def test_browser_startup_survives_missing_models(monkeypatch):
    warnings = []

    def boom():
        raise SystemExit("✖ LLM model 'front-model:8b' is not available in Ollama")

    monkeypatch.setattr(bot_browser, "_preflight_local", boom)
    monkeypatch.setattr(bot_browser.logger, "warning", warnings.append)
    bot_browser._preflight_startup()
    assert warnings and "front-model:8b" in warnings[0]
