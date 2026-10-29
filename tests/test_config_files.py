"""Config files: `.locatrc` holds settings, `.env` holds the three hosted keys.

Load order is `.locatrc` then `.env`, both as dotenv defaults, so the shell
environment wins over both and `.locatrc` wins over `.env` for the same name.
"""

from __future__ import annotations

import re
from pathlib import Path

import config

REPO_ROOT = Path(__file__).resolve().parent.parent
HOSTED_KEYS = ("OPENAI_API_KEY", "DEEPGRAM_API_KEY", "CARTESIA_API_KEY")


def test_locatrc_loaded_then_env(tmp_path, monkeypatch):
    """Settings from `.locatrc`, keys from `.env`, shell env beats both."""
    (tmp_path / ".locatrc").write_text(
        "LOCAT_GREETING=from locatrc\nLOCAT_LOG_LEVEL=INFO\n"
    )
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=sk-from-env-file\nLOCAT_LOG_LEVEL=WARNING\n"
    )
    monkeypatch.delenv("LOCAT_ENV_FILE", raising=False)
    monkeypatch.delenv("LOCAT_GREETING", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("LOCAT_LOG_LEVEL", "DEBUG")  # shell wins

    config._load_config_files(tmp_path)

    assert config.greeting() == "from locatrc"
    import os

    assert os.environ["OPENAI_API_KEY"] == "sk-from-env-file"
    assert config.log_level() == "DEBUG"


def test_legacy_settings_in_env_detected(tmp_path):
    """LOCAT_* settings left in `.env` are named so they can be moved."""
    (tmp_path / ".env").write_text(
        "# keys only\nOPENAI_API_KEY=sk-x\nLOCAT_LLM_MODEL=x\n"
    )
    assert config.legacy_settings_in_env(tmp_path) == ["LOCAT_LLM_MODEL"]


def test_legacy_settings_in_env_empty_when_keys_only(tmp_path):
    (tmp_path / ".env").write_text("\n".join(f"{k}=v" for k in HOSTED_KEYS))
    assert config.legacy_settings_in_env(tmp_path) == []


def test_env_example_lists_only_keys():
    """`env.example` documents exactly the three hosted keys, no values."""
    text = (REPO_ROOT / "env.example").read_text()
    names = [
        line.split("=", 1)[0].strip()
        for line in text.splitlines()
        if "=" in line and not line.strip().startswith("#")
    ]
    assert sorted(names) == sorted(HOSTED_KEYS)
    for line in text.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            assert line.split("=", 1)[1].strip() == "", f"value committed: {line}"


def test_locatrc_example_exists_and_has_settings():
    """The settings example moved to `locatrc.example` and points at tunables.py."""
    text = (REPO_ROOT / "locatrc.example").read_text()
    assert "LOCAT_MODEL_DIR" in text
    assert "tunables.py" in text
    assert not re.search(r"^\s*#?\s*(OPENAI|DEEPGRAM|CARTESIA)_API_KEY\s*=", text, re.M)
