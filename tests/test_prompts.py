"""Tests for prompt composition: each front prompt opens with its own identity."""

import tunables

OFFLINE_IDENTITY = (
    "You are a private financial thinking partner. You run entirely offline"
)
ONLINE_IDENTITY = (
    "You are a private financial thinking partner. This session is running in online mode"
)


def test_split_front_prompt_opens_with_the_offline_identity():
    assert tunables.SPLIT_FRONT_PROMPT.startswith(OFFLINE_IDENTITY)


def test_online_split_prompts_open_with_their_identities():
    assert tunables.LOFI_ONLINE_FRONT_PROMPT.startswith(ONLINE_IDENTITY)
    assert tunables.LOFI_ONLINE_ANALYST_PROMPT.startswith("You are a documents analyst")


def test_simple_chat_prompt_carries_the_redirect_line():
    assert (
        "I can't look that up in this mode — restart me in the full finances mode."
        in tunables.LOFI_SIMPLE_CHAT_PROMPT
    )


def test_simple_chat_prompt_is_date_grounded():
    assert tunables.simple_chat_prompt().startswith(tunables.LOFI_SIMPLE_CHAT_PROMPT)
    assert "Today's date is" in tunables.simple_chat_prompt()


def test_offline_cascade_prompt_opens_with_the_offline_identity():
    assert tunables.LOFI_OFFLINE_CASCADE_PROMPT.startswith(OFFLINE_IDENTITY)


def test_offline_cascade_prompt_names_the_tools():
    for tool in ("lookup", "filter_rows", "aggregate", "semantic_search"):
        assert tool in tunables.LOFI_OFFLINE_CASCADE_PROMPT
