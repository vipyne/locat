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


def write_fake_manifest(store: Path, name: str, tag: str, total_bytes: int) -> None:
    import json

    manifest = store / "manifests" / "registry.ollama.ai" / "library" / name / tag
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "layers": [
                    {
                        "mediaType": "application/vnd.ollama.image.model",
                        "digest": "sha256:deadbeef",
                        "size": total_bytes,
                    }
                ]
            }
        )
    )


def test_installed_model_size_is_decimal_gb_like_every_other_catalog_source(tmp_path):
    """The manifest fallback must agree with `ollama list`, fetch_catalog.py and the
    seed array, which are all decimal GB. 15e9 bytes = 15 GB = 13.97 GiB, so a
    binary computation mislabeled "GB" would print 14."""
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / ".llm-catalog").write_text("# generated 9999999999 2099-01-01 models=1\ntiny:3b|2||\n")
    write_fake_manifest(model_dir / "ollama", "fakeinstalled", "9b", 15_000_000_000)
    env = {
        **os.environ,
        "LOCAT_ENV_FILE": "/dev/null",
        "LOCAT_MODEL_DIR": str(model_dir),
        "OLLAMA_MODELS": str(model_dir / "ollama"),
        "OLLAMA_HOST": f"127.0.0.1:{closed_port()}",
        "LOCAT_CATALOG_MAX_AGE_DAYS": "0",
        "LOCAT_CONFIGURE_RAM_GB": "48",
    }
    result = subprocess.run(
        [str(REPO / "configure.sh"), "--catalogs"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stderr
    row = next(l for l in result.stdout.splitlines() if "fakeinstalled:9b" in l)
    assert "~15 GB" in row, row
