import sys
from pathlib import Path

import config
from scripts.print_models import OUTSIDE_FLAG, _path_line


def make_store(tmp_path, monkeypatch):
    store = tmp_path / "models"
    (store / "huggingface").mkdir(parents=True)
    monkeypatch.setattr(config, "MODELS_DIR", store)
    return store


def test_owned_path_prints_plain(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    owned = store / "huggingface" / "weights.bin"
    owned.write_bytes(b"x")

    line = _path_line(owned)

    assert str(owned) in line
    assert OUTSIDE_FLAG not in line
    assert "borrowed" not in line


def test_borrowed_symlink_shows_real_path(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    real = tmp_path / "home-cache" / "weights.bin"
    real.parent.mkdir()
    real.write_bytes(b"x")
    link = store / "huggingface" / "weights.bin"
    link.symlink_to(real)

    line = _path_line(link)

    assert f"(borrowed → {real}" in line
    assert OUTSIDE_FLAG not in line


def test_path_outside_store_still_flagged(tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    outside = tmp_path / "elsewhere" / "weights.bin"
    outside.parent.mkdir()
    outside.write_bytes(b"x")

    line = _path_line(outside)

    assert OUTSIDE_FLAG in line


def make_hf_cache_model(hub: Path, repo: str, size: int = 1024) -> Path:
    model = hub / f"models--{repo.replace('/', '--')}"
    blobs = model / "blobs"
    snapshot = model / "snapshots" / "abc"
    blobs.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    (blobs / "w").write_bytes(b"x" * size)
    (snapshot / "weights.safetensors").symlink_to("../../blobs/w")
    return model


def make_ollama_model(store: Path, name: str, tag: str, size: int = 2048) -> Path:
    import json as _json

    blob = store / "blobs" / "sha256-abc123"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"o" * size)
    manifest = store / "manifests" / "registry.ollama.ai" / "library" / name / tag
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        _json.dumps(
            {"layers": [{"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:abc123", "size": size}]}
        )
    )
    return blob


def inventory_env(tmp_path, monkeypatch):
    from scripts import print_models

    store = tmp_path / "models"
    (store / "huggingface" / "hub").mkdir(parents=True)
    monkeypatch.setattr(config, "MODELS_DIR", store)
    monkeypatch.setenv("HF_HOME", str(store / "huggingface"))
    monkeypatch.setenv("OLLAMA_MODELS", str(store / "ollama"))
    monkeypatch.setenv("LOCAT_KOKORO_MODEL_PATH", str(store / "kokoro" / "kokoro-v1.0.onnx"))
    monkeypatch.setenv("LOCAT_KOKORO_VOICES_PATH", str(store / "kokoro" / "voices-v1.0.bin"))
    monkeypatch.setenv("LOCAT_PIPER_DOWNLOAD_DIR", str(store / "piper"))
    monkeypatch.setattr(print_models, "_home_hf_hub", lambda: tmp_path / "home-hf" / "hub")
    monkeypatch.setattr(print_models, "_home_ollama_store", lambda: tmp_path / "home-ollama")
    return store


def test_downloaded_entries_cover_all_stores(tmp_path, monkeypatch):
    from scripts.print_models import downloaded_entries

    store = inventory_env(tmp_path, monkeypatch)
    make_hf_cache_model(store / "huggingface" / "hub", "mlx-community/whisper-x")
    make_ollama_model(store / "ollama", "qwen2.5", "14b")
    (store / "kokoro").mkdir()
    (store / "kokoro" / "kokoro-v1.0.onnx").write_bytes(b"k" * 512)

    entries = downloaded_entries()

    by_kind = {e["kind"]: e for e in entries}
    assert by_kind["hf"]["name"] == "mlx-community/whisper-x"
    assert by_kind["ollama"]["name"] == "qwen2.5:14b"
    assert by_kind["ollama"]["bytes"] == 2048
    assert by_kind["kokoro"]["bytes"] == 512
    for e in entries:
        assert Path(e["path"]).exists()


def test_downloaded_entries_include_external_stores_deduped(tmp_path, monkeypatch):
    from scripts.print_models import downloaded_entries

    store = inventory_env(tmp_path, monkeypatch)
    home_hub = tmp_path / "home-hf" / "hub"
    home_hub.mkdir(parents=True)
    external = make_hf_cache_model(home_hub, "org/only-at-home")
    shared = make_hf_cache_model(home_hub, "org/borrowed-one")
    (store / "huggingface" / "hub" / shared.name).symlink_to(shared)
    make_ollama_model(tmp_path / "home-ollama", "llama3.2", "1b")

    entries = downloaded_entries()

    names = [e["name"] for e in entries]
    assert names.count("org/borrowed-one") == 1
    assert "org/only-at-home" in names
    assert "llama3.2:1b" in names


def test_downloaded_entries_empty_stores(tmp_path, monkeypatch):
    from scripts.print_models import downloaded_entries

    inventory_env(tmp_path, monkeypatch)
    assert downloaded_entries() == []


def test_downloaded_output_names_outside_flag_once(tmp_path, monkeypatch, capsys):
    from scripts.print_models import _print_downloaded

    store = inventory_env(tmp_path, monkeypatch)
    make_hf_cache_model(store / "huggingface" / "hub", "org/inside-model")
    home_hub = tmp_path / "home-hf" / "hub"
    home_hub.mkdir(parents=True)
    make_hf_cache_model(home_hub, "org/home-model-a")
    make_hf_cache_model(home_hub, "org/home-model-b")

    _print_downloaded()
    out = capsys.readouterr().out

    assert out.count("outside LOCAT_MODEL_DIR") == 1
    outside_rows = [l for l in out.splitlines() if "home-model" in l]
    assert len(outside_rows) == 2
    for row in outside_rows:
        assert row.rstrip().endswith("⚠️")
    inside_row = next(l for l in out.splitlines() if "inside-model" in l)
    assert "⚠️" not in inside_row


def test_downloaded_output_no_legend_when_all_inside(tmp_path, monkeypatch, capsys):
    from scripts.print_models import _print_downloaded

    store = inventory_env(tmp_path, monkeypatch)
    make_hf_cache_model(store / "huggingface" / "hub", "org/inside-model")

    _print_downloaded()
    out = capsys.readouterr().out

    assert "outside LOCAT_MODEL_DIR" not in out
    assert "⚠️" not in out


def test_dash_d_is_alias_for_downloaded(tmp_path, monkeypatch, capsys):
    from scripts.print_models import main

    inventory_env(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["print_models.py", "-d"])

    main()

    assert "no models downloaded yet" in capsys.readouterr().out


def test_human_size_uses_binary_unit_labels():
    from scripts.print_models import _human_size

    assert _human_size(3 * 2**30) == "3.0 GiB"
    assert _human_size(5 * 2**20) == "5 MiB"
    assert _human_size(7 * 2**10) == "7 KiB"
