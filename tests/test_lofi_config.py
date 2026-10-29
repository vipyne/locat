import json
import sys

import config
import scripts.print_models as print_models
from bot_browser import CONFIG_MESSAGE_TYPE, _lofi_config_message, build_transport_params


def test_transport_params_offer_websocket_and_webrtc():
    params = build_transport_params()
    assert "websocket" in params
    assert "webrtc" in params  # aiortc installed in the dev env
    assert params["webrtc"]().audio_in_enabled is True
    assert params["webrtc"]().audio_out_enabled is True


def test_webrtc_transport_omitted_without_aiortc(monkeypatch):
    monkeypatch.setitem(sys.modules, "aiortc", None)
    params = build_transport_params()
    assert "websocket" in params
    assert "webrtc" not in params


def test_split_architecture_adds_analyst_row(monkeypatch):
    monkeypatch.setenv("LOFI_ARCHITECTURE", "llmwithbackend")
    monkeypatch.setenv("LOFI_RAG_LLM_MODEL", "nemotron-3-nano:4b")
    message = _lofi_config_message()
    roles = [m["role"] for m in message["models"]]
    assert roles == ["STT", "LLM", "ANALYST", "TTS", "EMBED"]
    analyst = message["models"][roles.index("ANALYST")]
    assert "nemotron-3-nano:4b" in analyst["model"]


def test_lofi_config_message_shape():
    message = _lofi_config_message()
    json.dumps(message)
    assert message["type"] == CONFIG_MESSAGE_TYPE == "lofi-config"
    assert message["mode"] == "offline"
    # Default architecture is split — the analyst backend gets its own row.
    assert [m["role"] for m in message["models"]] == [
        "STT",
        "LLM",
        "ANALYST",
        "TTS",
        "EMBED",
    ]
    for entry in message["models"]:
        assert entry["model"]
        assert entry["path"].lstrip().startswith("→")
    assert message["ollama_host"].startswith("http")
    assert isinstance(message["ollama_started_by_lofi"], bool)
    assert message["rag"].startswith(("index:", "no index"))


def test_config_message_carries_mode():
    assert _lofi_config_message("offline")["mode"] == "offline"
    assert _lofi_config_message("online")["mode"] == "online"


def test_online_model_entries_roles(monkeypatch):
    for key in ("OPENAI_API_KEY", "DEEPGRAM_API_KEY", "CARTESIA_API_KEY"):
        monkeypatch.setenv(key, f"fake-{key}")
    message = _lofi_config_message("online")
    roles = [m["role"] for m in message["models"]]
    assert roles == ["STT", "LLM", "TTS", "EMBED"]
    dumped = json.dumps(message)
    for entry in message["models"]:
        assert entry["path"] == "—"
        assert "cloud" in entry["model"]
    assert "fake-" not in dumped


def test_model_entries_missing_flag(monkeypatch, tmp_path):
    """A role whose weights resolve to nothing is flagged for the panel."""
    monkeypatch.setenv("LOFI_ARCHITECTURE", "llmwithbackend")
    weights = tmp_path / "weights.bin"
    weights.write_text("x")
    monkeypatch.setattr(print_models, "_ollama_file", lambda tag: None)
    monkeypatch.setattr(print_models, "_stt_file", lambda: weights)
    monkeypatch.setattr(print_models, "_tts_file", lambda: weights)
    entries = {e["role"]: e for e in print_models.model_entries()}
    assert entries["LLM"]["missing"] is True
    assert entries["ANALYST"]["missing"] is True
    assert entries["EMBED"]["missing"] is True
    assert entries["STT"]["missing"] is False
    assert entries["TTS"]["missing"] is False
    # Cloud rows have no local weights to miss.
    assert all(e["missing"] is False for e in print_models.model_entries("online"))


def test_architecture_accepts_simple_chat(monkeypatch):
    """The third offline architecture is a valid LOFI_ARCHITECTURE value."""
    monkeypatch.setenv("LOFI_ARCHITECTURE", "cascade-simple-chat")
    assert config.architecture() == "cascade-simple-chat"


def test_simple_chat_overrides_online_only(monkeypatch):
    """Simple-chat is offline-only by nature, so it wins over LOFI_ONLINE_ONLY.

    A workshop machine's `.lofirc` can carry LOFI_ONLINE_ONLY=1; with
    simple-chat selected the process must still serve offline sessions.
    """
    monkeypatch.setenv("LOFI_ONLINE_ONLY", "1")
    monkeypatch.setenv("LOFI_ARCHITECTURE", "cascade-simple-chat")
    assert config.online_only() is False
    assert config.serve_mode() != "online-only"
