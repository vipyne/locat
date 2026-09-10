import json

from bot_moq import CONFIG_MESSAGE_TYPE, _locat_config_message


def test_locat_config_message_shape():
    message = _locat_config_message()
    json.dumps(message)
    assert message["type"] == CONFIG_MESSAGE_TYPE == "locat-config"
    assert [m["role"] for m in message["models"]] == ["STT", "LLM", "TTS", "EMBED"]
    for entry in message["models"]:
        assert entry["model"]
        assert entry["path"].lstrip().startswith("→")
    assert message["ollama_host"].startswith("http")
    assert isinstance(message["ollama_started_by_locat"], bool)
    assert message["rag"].startswith(("index:", "no index"))
