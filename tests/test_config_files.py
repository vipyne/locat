"""Config files: `.lofirc` holds settings, `.env` holds the three hosted keys.

Load order is `.lofirc` then `.env`, both as dotenv defaults, so the shell
environment wins over both and `.lofirc` wins over `.env` for the same name.
"""

from __future__ import annotations

import re
from pathlib import Path

import config

REPO_ROOT = Path(__file__).resolve().parent.parent
HOSTED_KEYS = ("OPENAI_API_KEY", "DEEPGRAM_API_KEY", "CARTESIA_API_KEY")


def test_lofirc_loaded_then_env(tmp_path, monkeypatch):
    """Settings from `.lofirc`, keys from `.env`, shell env beats both."""
    (tmp_path / ".lofirc").write_text(
        "LOFI_GREETING=from lofirc\nLOFI_LOG_LEVEL=INFO\n"
    )
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=sk-from-env-file\nLOFI_LOG_LEVEL=WARNING\n"
    )
    monkeypatch.delenv("LOFI_ENV_FILE", raising=False)
    monkeypatch.delenv("LOFI_GREETING", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("LOFI_LOG_LEVEL", "DEBUG")  # shell wins

    config._load_config_files(tmp_path)

    assert config.greeting() == "from lofirc"
    import os

    assert os.environ["OPENAI_API_KEY"] == "sk-from-env-file"
    assert config.log_level() == "DEBUG"


def test_legacy_settings_in_env_detected(tmp_path):
    """LOFI_* settings left in `.env` are named so they can be moved."""
    (tmp_path / ".env").write_text(
        "# keys only\nOPENAI_API_KEY=sk-x\nLOFI_LLM_MODEL=x\n"
    )
    assert config.legacy_settings_in_env(tmp_path) == ["LOFI_LLM_MODEL"]


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


def test_lofirc_example_exists_and_has_settings():
    """The settings example moved to `lofirc.example` and points at tunables.py."""
    text = (REPO_ROOT / "lofirc.example").read_text()
    assert "LOFI_MODEL_DIR" in text
    assert "tunables.py" in text
    assert not re.search(r"^\s*#?\s*(OPENAI|DEEPGRAM|CARTESIA)_API_KEY\s*=", text, re.M)
