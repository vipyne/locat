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
# generated 9999999999 2099-01-01 models=7
brand-coder:32b|20||
mathstral-x:24b|15||
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
        "LOFI_ENV_FILE": "/dev/null",
        "LOFI_MODEL_DIR": str(model_dir),
        "LOFI_CATALOG_MAX_AGE_DAYS": "0",
        "LOFI_CONFIGURE_RAM_GB": "48",
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


def test_specialized_families_filtered_by_name(tmp_path):
    """brand-coder:32b is the largest fit; the name filter keeps it out of the
    listing and every tier (quality stays moe-big:30b)."""
    stdout = run_configure(tmp_path)
    tiers = cascade_llm_tags(stdout)

    assert "brand-coder:32b" not in stdout
    assert "mathstral-x:24b" not in stdout
    assert tiers["best quality"] == "moe-big:30b"


def test_fetch_catalog_keep_rejects_specialized_names():
    from scripts.fetch_catalog import keep

    assert not keep("qwen3-coder", {"tools"}, [30.0])
    assert not keep("mathstral", set(), [7.0])
    assert not keep("llama-guard3", set(), [8.0])
    assert not keep("sqlcoder", set(), [15.0])
    assert keep("qwen3", {"tools", "thinking"}, [30.0])
    assert keep("gemma3", {"vision"}, [27.0])


def test_fetch_catalog_keep_rejects_stale_families_keeps_unknown_age():
    from scripts.fetch_catalog import MAX_AGE_DAYS, UNKNOWN_AGE, keep

    assert not keep("qwen", {"tools"}, [32.0], age=MAX_AGE_DAYS + 1)
    assert keep("qwen3", {"tools"}, [30.0], age=MAX_AGE_DAYS)
    assert keep("fresh-model", set(), [7.0], age=0)
    assert keep("age-unparsed", set(), [7.0], age=UNKNOWN_AGE)


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
        "LOFI_ENV_FILE": "/dev/null",
        "LOFI_MODEL_DIR": str(model_dir),
        "OLLAMA_MODELS": str(model_dir / "ollama"),
        "OLLAMA_HOST": f"127.0.0.1:{closed_port()}",
        "LOFI_CATALOG_MAX_AGE_DAYS": "0",
        "LOFI_CONFIGURE_RAM_GB": "48",
    }
    result = subprocess.run(
        [str(REPO / "configure.sh"), "--catalogs"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stderr
    row = next(l for l in result.stdout.splitlines() if "fakeinstalled:9b" in l)
    assert "~15 GB" in row, row
