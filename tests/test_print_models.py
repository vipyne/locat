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
