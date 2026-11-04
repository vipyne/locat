"""Tests for prompt composition: each front prompt opens with its own identity."""

import tunables

OFFLINE_IDENTITY = (
    "You are a private records and thinking partner. You run entirely offline"
)
ONLINE_IDENTITY = (
    "You are a private financial thinking partner. This session is running in online mode"
)
REDIRECT_LINE = (
    "I can't look that up in this mode — restart me in the full records mode."
)


def test_split_front_prompt_opens_with_the_offline_identity():
    assert tunables.SPLIT_FRONT_PROMPT.startswith(OFFLINE_IDENTITY)


def test_online_split_prompts_open_with_their_identities():
    assert tunables.LOCAT_ONLINE_FRONT_PROMPT.startswith(ONLINE_IDENTITY)
    assert tunables.LOCAT_ONLINE_ANALYST_PROMPT.startswith("You are a documents analyst")


def test_simple_chat_prompt_carries_the_redirect_line():
    assert REDIRECT_LINE in tunables.LOCAT_SIMPLE_CHAT_PROMPT


def test_simple_chat_prompt_is_date_grounded():
    assert tunables.simple_chat_prompt().startswith(tunables.LOCAT_SIMPLE_CHAT_PROMPT)
    assert "Today's date is" in tunables.simple_chat_prompt()


def test_offline_cascade_prompt_opens_with_the_offline_identity():
    assert tunables.LOCAT_OFFLINE_CASCADE_PROMPT.startswith(OFFLINE_IDENTITY)


def test_offline_cascade_prompt_names_the_tools():
    for tool in ("lookup", "filter_rows", "aggregate", "semantic_search"):
        assert tool in tunables.LOCAT_OFFLINE_CASCADE_PROMPT


def test_offline_identity_paragraph_is_shared_byte_for_byte():
    """Cascade and split open on the SAME identity paragraph, deliberately."""
    cascade = tunables.LOCAT_OFFLINE_CASCADE_PROMPT.split("\n\n")[0]
    split = tunables.SPLIT_FRONT_PROMPT.split("\n\n")[0]

    assert cascade == split


def test_offline_identity_promises_only_that_records_stay_local():
    """Audio can cross the network under a mixed session; records cannot."""
    for prompt in (tunables.LOCAT_OFFLINE_CASCADE_PROMPT, tunables.SPLIT_FRONT_PROMPT):
        assert "their records never leave this machine" in prompt
        assert "nothing they say leaves this machine" not in prompt


def test_offline_prompts_drop_the_financial_partner_identity():
    for prompt in (
        tunables.LOCAT_GREETING,
        tunables.LOCAT_OFFLINE_CASCADE_PROMPT,
        tunables.SPLIT_FRONT_PROMPT,
        tunables.LOCAT_SIMPLE_CHAT_PROMPT,
        tunables.ANALYST_SYSTEM_PROMPT,
    ):
        assert "financial thinking partner" not in prompt
        assert "finance assistant" not in prompt


def test_analyst_prompt_opens_on_a_persona_neutral_framing():
    assert tunables.ANALYST_SYSTEM_PROMPT.startswith(
        "You are a records analyst for a private, offline assistant."
    )


def test_simple_chat_prompt_still_forbids_inventing_records():
    assert "Never invent a record" in tunables.LOCAT_SIMPLE_CHAT_PROMPT
    assert "no access to their records" in tunables.LOCAT_SIMPLE_CHAT_PROMPT
