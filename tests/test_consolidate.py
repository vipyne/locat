from pathlib import Path

from scripts.consolidate import consolidate


def make_hf_model(hub: Path, repo: str, complete: bool = True) -> Path:
    model = hub / f"models--{repo.replace('/', '--')}"
    blobs = model / "blobs"
    snapshot = model / "snapshots" / "abc123"
    blobs.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    (blobs / "cfg").write_text("{}")
    (snapshot / "config.json").symlink_to("../../blobs/cfg")
    if complete:
        (blobs / "weights").write_bytes(b"w" * 64)
        (snapshot / "weights.safetensors").symlink_to("../../blobs/weights")
    else:
        (blobs / "weights.deadbeef.incomplete").write_bytes(b"")
    return model


def make_ollama_store(store: Path, with_model: bool) -> Path:
    manifests = store / "manifests" / "registry.ollama.ai" / "library" / "somellm"
    (store / "blobs").mkdir(parents=True)
    if with_model:
        manifests.mkdir(parents=True)
        (manifests / "latest").write_text("{}")
    else:
        (store / "manifests").mkdir(parents=True)
    return store


def make_dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    model_dir = tmp_path / "models"
    (model_dir / "huggingface" / "hub").mkdir(parents=True)
    ext_hub = tmp_path / "home-cache" / "hub"
    ext_hub.mkdir(parents=True)
    ext_ollama = tmp_path / "home-ollama"
    return model_dir, ext_hub, ext_ollama


def run(model_dir, ext_hub, ext_ollama, **kwargs):
    return consolidate(
        model_dir=model_dir,
        external_hf_hub=ext_hub,
        external_ollama=ext_ollama,
        ollama_store_in_use=kwargs.pop("ollama_store_in_use", False),
        dry_run=kwargs.pop("dry_run", False),
    )


def kinds(actions) -> list[str]:
    return [a.kind for a in actions]


def test_borrows_external_hf_model(tmp_path):
    model_dir, ext_hub, ext_ollama = make_dirs(tmp_path)
    external = make_hf_model(ext_hub, "org/model-a")

    actions = run(model_dir, ext_hub, ext_ollama)

    link = model_dir / "huggingface" / "hub" / "models--org--model-a"
    assert "hf-borrow" in kinds(actions)
    assert link.is_symlink()
    assert link.resolve() == external.resolve()
    assert (link / "snapshots" / "abc123" / "weights.safetensors").exists()


def test_leaves_owned_complete_model_alone(tmp_path):
    model_dir, ext_hub, ext_ollama = make_dirs(tmp_path)
    owned = make_hf_model(model_dir / "huggingface" / "hub", "org/model-a")
    make_hf_model(ext_hub, "org/model-a")

    actions = run(model_dir, ext_hub, ext_ollama)

    assert "hf-borrow" not in kinds(actions)
    assert not owned.is_symlink()
    assert (owned / "blobs" / "weights").exists()


def test_replaces_broken_partial_with_borrow(tmp_path):
    model_dir, ext_hub, ext_ollama = make_dirs(tmp_path)
    partial = make_hf_model(model_dir / "huggingface" / "hub", "org/model-a", complete=False)
    external = make_hf_model(ext_hub, "org/model-a")

    actions = run(model_dir, ext_hub, ext_ollama)

    assert "hf-replace-partial" in kinds(actions)
    assert partial.is_symlink()
    assert partial.resolve() == external.resolve()


def test_keeps_broken_partial_when_no_external_copy(tmp_path):
    model_dir, ext_hub, ext_ollama = make_dirs(tmp_path)
    partial = make_hf_model(model_dir / "huggingface" / "hub", "org/model-a", complete=False)

    actions = run(model_dir, ext_hub, ext_ollama)

    assert not partial.is_symlink()
    assert "hf-replace-partial" not in kinds(actions)


def test_adopts_external_ollama_store(tmp_path):
    model_dir, ext_hub, ext_ollama = make_dirs(tmp_path)
    make_ollama_store(ext_ollama, with_model=True)

    actions = run(model_dir, ext_hub, ext_ollama)

    store = model_dir / "ollama"
    assert "ollama-borrow" in kinds(actions)
    assert store.is_symlink()
    assert store.resolve() == ext_ollama.resolve()


def test_ollama_conflict_when_both_have_models(tmp_path):
    model_dir, ext_hub, ext_ollama = make_dirs(tmp_path)
    make_ollama_store(ext_ollama, with_model=True)
    make_ollama_store(model_dir / "ollama", with_model=True)

    actions = run(model_dir, ext_hub, ext_ollama)

    assert "ollama-conflict" in kinds(actions)
    assert not (model_dir / "ollama").is_symlink()


def test_ollama_skipped_while_store_in_use(tmp_path):
    model_dir, ext_hub, ext_ollama = make_dirs(tmp_path)
    make_ollama_store(ext_ollama, with_model=True)

    actions = run(model_dir, ext_hub, ext_ollama, ollama_store_in_use=True)

    assert "ollama-in-use" in kinds(actions)
    assert not (model_dir / "ollama").exists()


def test_second_run_is_idempotent(tmp_path):
    model_dir, ext_hub, ext_ollama = make_dirs(tmp_path)
    make_hf_model(ext_hub, "org/model-a")
    make_ollama_store(ext_ollama, with_model=True)

    first = run(model_dir, ext_hub, ext_ollama)
    second = run(model_dir, ext_hub, ext_ollama)

    changing = {"hf-borrow", "hf-replace-partial", "ollama-borrow"}
    assert changing & set(kinds(first))
    assert not changing & set(kinds(second))
    link = model_dir / "huggingface" / "hub" / "models--org--model-a"
    assert link.is_symlink()
    assert (model_dir / "ollama").is_symlink()


def test_dry_run_touches_nothing(tmp_path):
    model_dir, ext_hub, ext_ollama = make_dirs(tmp_path)
    make_hf_model(ext_hub, "org/model-a")
    make_ollama_store(ext_ollama, with_model=True)
    partial = make_hf_model(model_dir / "huggingface" / "hub", "org/model-b", complete=False)

    actions = run(model_dir, ext_hub, ext_ollama, dry_run=True)

    assert {"hf-borrow", "ollama-borrow"} <= set(kinds(actions))
    assert not (model_dir / "huggingface" / "hub" / "models--org--model-a").exists()
    assert not (model_dir / "ollama").exists()
    assert list((partial / "blobs").glob("*.incomplete"))


def test_missing_external_stores_do_nothing(tmp_path):
    model_dir, ext_hub, ext_ollama = make_dirs(tmp_path)

    actions = run(model_dir, ext_hub, ext_ollama)

    changing = {"hf-borrow", "hf-replace-partial", "ollama-borrow"}
    assert not changing & set(kinds(actions))


def test_replaces_partial_even_when_external_has_stray_stubs(tmp_path):
    model_dir, ext_hub, ext_ollama = make_dirs(tmp_path)
    partial = make_hf_model(model_dir / "huggingface" / "hub", "org/model-a", complete=False)
    external = make_hf_model(ext_hub, "org/model-a")
    (external / "blobs" / "weights.stray.incomplete").write_bytes(b"")

    actions = run(model_dir, ext_hub, ext_ollama)

    assert "hf-replace-partial" in kinds(actions)
    assert partial.is_symlink()


def test_external_with_dangling_snapshot_is_not_adopted_over_partial(tmp_path):
    model_dir, ext_hub, ext_ollama = make_dirs(tmp_path)
    partial = make_hf_model(model_dir / "huggingface" / "hub", "org/model-a", complete=False)
    external = make_hf_model(ext_hub, "org/model-a")
    (external / "blobs" / "weights").unlink()

    actions = run(model_dir, ext_hub, ext_ollama)

    assert "hf-replace-partial" not in kinds(actions)
    assert not partial.is_symlink()


def test_external_ollama_falls_back_when_env_points_nowhere(tmp_path, monkeypatch):
    from scripts.consolidate import _default_external_ollama

    home = tmp_path / "home"
    store = home / ".ollama" / "models"
    store.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "does-not-exist"))

    assert _default_external_ollama(tmp_path / "models") == store
