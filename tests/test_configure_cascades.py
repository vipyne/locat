import os
import socket
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

CATALOG = """\
# generated 9999999999 2099-01-01 models=5
moe-big:30b|19|MoE, 2b active|2
noted-thinker:32b|20|thinking|
deepseek-r1:16b|10||
dense-mid:14b|9||
tiny:3b|2||
"""


def run_configure(tmp_path: Path) -> str:
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / ".llm-catalog").write_text(CATALOG)
    env = {
        **os.environ,
        "LOCAT_ENV_FILE": "/dev/null",
        "LOCAT_MODEL_DIR": str(model_dir),
        "LOCAT_CATALOG_MAX_AGE_DAYS": "0",
        "LOCAT_CONFIGURE_RAM_GB": "48",
        "OLLAMA_HOST": f"127.0.0.1:{closed_port()}",
        "OLLAMA_MODELS": str(model_dir / "ollama"),
    }
    result = subprocess.run(
        [str(REPO / "configure.sh")],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def cascade_llm_tags(stdout: str) -> dict[str, str]:
    tiers = {}
    current = None
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped in ("balanced", "best quality", "snappiest"):
            current = stripped
        elif current and stripped.startswith("LLM"):
            tiers[current] = stripped.split()[2]
            current = None
    return tiers


def test_all_three_tiers_suggested_even_when_one_model_tops_every_metric(tmp_path):
    tiers = cascade_llm_tags(run_configure(tmp_path))

    assert set(tiers) == {"balanced", "best quality", "snappiest"}
    assert len(set(tiers.values())) == 3


def test_quality_is_largest_and_snappy_is_fastest(tmp_path):
    tiers = cascade_llm_tags(run_configure(tmp_path))

    assert tiers["best quality"] == "moe-big:30b"
    assert tiers["snappiest"] == "tiny:3b"
    assert tiers["balanced"] == "dense-mid:14b"


def test_reasoning_models_never_recommended_even_without_catalog_note(tmp_path):
    tiers = cascade_llm_tags(run_configure(tmp_path))

    assert "deepseek-r1:16b" not in tiers.values()
    assert "noted-thinker:32b" not in tiers.values()
