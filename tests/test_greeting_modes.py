"""Greeting per session mode: offline keeps today's line, online gets its own.

The online greeting is spoken on a cloud cascade over the public documents
only, so it must never claim offline or private operation.
"""

import bot_browser
import config
import tunables
from bot_browser import OFFLINE_MODE
from pipeline import ONLINE_MODE


def test_online_greeting_differs_from_offline(monkeypatch):
    """Online names the online/cloud session; it never says "offline"."""
    monkeypatch.delenv("LOCAT_GREETING", raising=False)
    monkeypatch.delenv("LOCAT_ONLINE_GREETING", raising=False)

    online = config.online_greeting()

    assert online != config.greeting()
    assert "offline" not in online.lower()
    assert "online" in online.lower()


def test_online_greeting_env_override(monkeypatch):
    """`LOCAT_ONLINE_GREETING` wins over the tunables default."""
    monkeypatch.setenv("LOCAT_ONLINE_GREETING", "hi from the cloud")

    assert config.online_greeting() == "hi from the cloud"


def test_greeting_for_mode(monkeypatch):
    """`_greeting(mode)` picks the mode's line; anything else is offline."""
    monkeypatch.delenv("LOCAT_GREETING", raising=False)
    monkeypatch.delenv("LOCAT_ONLINE_GREETING", raising=False)

    assert bot_browser._greeting(ONLINE_MODE) == config.online_greeting()
    assert bot_browser._greeting(OFFLINE_MODE) == config.greeting()
    assert bot_browser._greeting("garbage") == config.greeting()


def test_greeting_for_mode_simple_chat(monkeypatch):
    """Simple-chat sessions open with the chat-only line, not the finances one."""
    monkeypatch.setenv("LOCAT_ARCHITECTURE", "cascade-simple-chat")
    monkeypatch.delenv("LOCAT_GREETING", raising=False)
    monkeypatch.delenv("LOCAT_SIMPLE_CHAT_GREETING", raising=False)

    assert bot_browser._greeting(OFFLINE_MODE) == config.simple_chat_greeting()


def test_simple_chat_greeting_selected_by_architecture(monkeypatch):
    """Simple-chat's greeting wins even when LOCAT_GREETING is set."""
    monkeypatch.setenv("LOCAT_ARCHITECTURE", "cascade-simple-chat")
    monkeypatch.setenv("LOCAT_GREETING", "finances greeting from locatrc")

    assert config.offline_greeting() == config.simple_chat_greeting()


def test_offline_greeting_default_is_finances_greeting(monkeypatch):
    """Every other architecture keeps the finances greeting."""
    monkeypatch.delenv("LOCAT_ARCHITECTURE", raising=False)

    assert config.offline_greeting() == config.greeting()


def test_simple_chat_greeting_never_mentions_finances():
    """Chat-only mode has no records access, so its opener promises none."""
    assert "financ" not in tunables.LOCAT_SIMPLE_CHAT_GREETING.lower()


def test_offline_greeting_drops_the_financial_partner_identity(monkeypatch):
    """Offline opens as a private records partner, not a finance one."""
    monkeypatch.delenv("LOCAT_GREETING", raising=False)
    monkeypatch.delenv("LOCAT_ARCHITECTURE", raising=False)

    opener = config.greeting()

    assert "financial thinking partner" not in opener
    assert "records" in opener.lower()
    assert "offline" in opener.lower()
