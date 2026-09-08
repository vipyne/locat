import pytest

import bot
import config
import services
from scripts.print_models import stt_repo_id

REPO = "mlx-community/whisper-large-v3-turbo"
REPO_DIRNAME = "models--mlx-community--whisper-large-v3-turbo"


def _add_snapshot(hub, *filenames, dangling=False):
    snap = hub / REPO_DIRNAME / "snapshots" / "abc123"
    snap.mkdir(parents=True, exist_ok=True)
    for filename in filenames:
        if dangling:
            (snap / filename).symlink_to(hub / REPO_DIRNAME / "blobs" / "sha-gone")
        else:
            (snap / filename).write_bytes(b"w" * 64)
    return snap


@pytest.fixture
def hubs(monkeypatch, tmp_path):
    store_hub = tmp_path / "store" / "hub"
    store_hub.mkdir(parents=True)
    monkeypatch.setenv("LOCAT_STT_ENGINE", "whisper_mlx")
    monkeypatch.setenv("LOCAT_WHISPER_MODEL", "LARGE_V3_TURBO")
    monkeypatch.setenv("HF_HOME", str(store_hub.parent))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "external-cache"))
    return store_hub, tmp_path / "external-cache" / "huggingface" / "hub"


def test_stt_repo_id_resolves_whisper_mlx(hubs):
    assert stt_repo_id() == REPO


def test_weights_in_store_pass(hubs):
    store_hub, _ = hubs
    _add_snapshot(store_hub, "config.json", "weights.safetensors")
    bot._preflight_stt()


def test_weights_only_in_external_cache_pass(hubs):
    _, external_hub = hubs
    _add_snapshot(external_hub, "weights.safetensors")
    bot._preflight_stt()


def test_missing_weights_exit_with_prefetch_remedy(hubs):
    with pytest.raises(SystemExit) as excinfo:
        bot._preflight_stt()
    assert "scripts/prefetch_models.py" in str(excinfo.value.code)
    assert REPO in str(excinfo.value.code)
    assert "consolidate" not in str(excinfo.value.code)


def test_metadata_only_snapshot_exits(hubs):
    store_hub, _ = hubs
    _add_snapshot(store_hub, "config.json")
    with pytest.raises(SystemExit) as excinfo:
        bot._preflight_stt()
    assert "scripts/prefetch_models.py" in str(excinfo.value.code)


def test_dangling_weight_symlink_exits(hubs):
    store_hub, _ = hubs
    _add_snapshot(store_hub, "weights.safetensors", dangling=True)
    with pytest.raises(SystemExit):
        bot._preflight_stt()


def test_moonshine_engine_is_skipped(hubs, monkeypatch):
    monkeypatch.setenv("LOCAT_STT_ENGINE", "moonshine")
    bot._preflight_stt()


def test_weights_location_prefers_store(hubs):
    store_hub, external_hub = hubs
    store_snap = _add_snapshot(store_hub, "weights.safetensors")
    _add_snapshot(external_hub, "weights.safetensors")
    assert services.stt_weights_location(REPO) == ("store", store_snap)


def test_model_arg_is_repo_id_when_store_has_weights(hubs):
    store_hub, _ = hubs
    _add_snapshot(store_hub, "weights.safetensors")
    assert services._stt_model_arg(REPO) == REPO


def test_model_arg_is_snapshot_path_when_external_only(hubs):
    _, external_hub = hubs
    snap = _add_snapshot(external_hub, "weights.safetensors")
    assert services._stt_model_arg(REPO) == str(snap)


@pytest.mark.skipif(not config.IS_APPLE_SILICON, reason="whisper_mlx needs Apple Silicon")
def test_build_stt_receives_external_snapshot_path(hubs):
    _, external_hub = hubs
    snap = _add_snapshot(external_hub, "weights.safetensors")
    service = services.build_stt()
    assert service._settings.model == str(snap)
