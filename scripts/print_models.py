#!/usr/bin/env python3
"""Print the exact STT/LLM/TTS engines + models the bot will load.

Shared by locat.sh and configure.sh so the printed values can never drift from
what the bot actually loads: everything resolves through config.py (which
loads ./.env), including the engine choice (LOCAT_STT_ENGINE / LOCAT_TTS_ENGINE) that
services.py dispatches on.

Each model line is followed by the full path of its weights on disk, found by
looking where the engine would actually load from — the configured stores AND
the tools' default caches — so the path shows where a model really is, not
where the config wishes it were. Files outside LOCAT_MODEL_DIR are flagged.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# Runnable from any CWD: make the repo root importable, then import config
# FIRST (it pins the repo-local model-cache env vars at import time).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

OUTSIDE_FLAG = "⚠️  outside LOCAT_MODEL_DIR"
NOT_DOWNLOADED = "(not downloaded yet)"

_WEIGHT_SUFFIXES = {".safetensors", ".bin", ".npz", ".onnx", ".pt"}


def _hf_caches() -> list[Path]:
    caches = [Path(os.environ["HF_HOME"]), Path.home() / ".cache" / "huggingface"]
    seen: list[Path] = []
    for c in caches:
        if c not in seen:
            seen.append(c)
    return seen


def _snapshot_dirs(snapshots: Path) -> list[Path]:
    """The repo's local snapshot dirs, the refs/main one first, rest newest-first."""
    dirs = sorted(
        (d for d in snapshots.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    ref = snapshots.parent / "refs" / "main"
    if ref.is_file():
        pinned = snapshots / ref.read_text().strip()
        if pinned.is_dir():
            dirs = [pinned] + [d for d in dirs if d != pinned]
    return dirs


def _newest_weights_file(snapshots: Path) -> Path | None:
    for snap in _snapshot_dirs(snapshots):
        files = [p for p in snap.rglob("*") if p.is_file()]
        if files:
            return max(files, key=lambda p: p.stat().st_size)
    return None


def hf_weights_snapshot(hub: Path, repo_id: str) -> Path | None:
    """The repo's best local snapshot dir under this hub that holds a real
    weight file. Metadata-only snapshots don't count, and neither do dangling
    symlinks into blobs/ — the trace an interrupted download leaves behind.
    """
    snapshots = hub / ("models--" + repo_id.replace("/", "--")) / "snapshots"
    if not snapshots.is_dir():
        return None
    for snap in _snapshot_dirs(snapshots):
        if any(p.suffix in _WEIGHT_SUFFIXES and p.is_file() for p in snap.rglob("*")):
            return snap
    return None


def _hf_repo_file(repo_id: str) -> Path | None:
    """The largest file in the repo's latest local snapshot, in any HF cache."""
    dirname = "models--" + repo_id.replace("/", "--")
    for cache in _hf_caches():
        snapshots = cache / "hub" / dirname / "snapshots"
        if snapshots.is_dir():
            found = _newest_weights_file(snapshots)
            if found:
                return found
    return None


def _hf_glob_file(dir_prefix: str) -> Path | None:
    for cache in _hf_caches():
        hub = cache / "hub"
        if not hub.is_dir():
            continue
        for d in sorted(hub.glob(f"{dir_prefix}*")):
            found = _newest_weights_file(d / "snapshots")
            if found:
                return found
    return None


def _faster_whisper_repo(value: str) -> str:
    if "/" in value:
        return value
    try:
        from faster_whisper.utils import _MODELS

        return _MODELS.get(value, f"Systran/faster-whisper-{value}")
    except ImportError:
        return f"Systran/faster-whisper-{value}"


def stt_repo_id() -> str | None:
    """HF repo id the configured STT engine loads weights from.

    None when the engine has no single fixed repo (moonshine) or the
    configuration doesn't resolve (pipecat missing, invalid model name).
    """
    engine = config.stt_engine()
    try:
        if engine == "whisper_mlx":
            from pipecat.services.whisper.stt import MLXModel

            return MLXModel[config.whisper_model()].value
        if engine == "faster_whisper":
            from pipecat.services.whisper.stt import Model

            return _faster_whisper_repo(Model[config.faster_whisper_model()].value)
    except (ImportError, KeyError):
        return None
    return None


def _stt_file() -> Path | None:
    if config.stt_engine() == "moonshine":
        return _hf_glob_file("models--UsefulSensors--moonshine")
    repo = stt_repo_id()
    return _hf_repo_file(repo) if repo else None


def _ollama_manifest(store: Path, tag: str) -> Path | None:
    name, _, variant = tag.partition(":")
    variant = variant or "latest"
    if name.startswith("hf.co/"):
        manifest = store / "manifests" / "hf.co" / name[len("hf.co/") :] / variant
    else:
        if "/" not in name:
            name = f"library/{name}"
        manifest = store / "manifests" / "registry.ollama.ai" / name / variant
    if not manifest.is_file():
        return None
    for layer in json.loads(manifest.read_text()).get("layers", []):
        if layer.get("mediaType", "").endswith("model"):
            return store / "blobs" / layer["digest"].replace(":", "-")
    return None


def _ollama_file(tag: str) -> Path | None:
    """An Ollama model's weights blob — from the RUNNING server when there is one.

    `ollama show` answers with the store the server actually uses, which may
    not be the configured OLLAMA_MODELS (e.g. a menu-bar Ollama.app serving
    from ~/.ollama). That live answer is the truth about where a pull lands.
    """
    try:
        out = subprocess.run(
            ["ollama", "show", "--modelfile", tag],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode == 0:
            for line in out.stdout.splitlines():
                blob = line.removeprefix("FROM ").strip()
                if line.startswith("FROM ") and blob.startswith("/"):
                    return Path(blob)
    except Exception:
        pass
    return _ollama_manifest(Path(config.ollama_models_dir()), tag)


def _tts_file() -> Path | None:
    if config.tts_engine() == "kokoro":
        return Path(config.kokoro_model_path())
    if config.tts_engine() == "piper":
        return Path(config.piper_download_dir()) / f"{config.piper_voice()}.onnx"
    return None


def _path_line(found: Path | None) -> str:
    if found is None:
        return f"  → {NOT_DOWNLOADED}"
    if not found.exists():
        return f"  → {found}   {NOT_DOWNLOADED}"
    store = Path(config.model_dir()).resolve()
    inside_store = _is_relative_to(Path(os.path.normpath(found)), store)
    inside_for_real = _is_relative_to(found.resolve(), store)
    if inside_for_real:
        return f"  → {found}"
    if inside_store:
        return f"  → {found}   (borrowed → {found.resolve()})"
    return f"  → {found}   {OUTSIDE_FLAG}"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _home_hf_hub() -> Path:
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    return cache_home / "huggingface" / "hub"


def _home_ollama_store() -> Path:
    return Path.home() / ".ollama" / "models"


def _hf_store_models(hub: Path):
    for model in sorted(hub.glob("models--*")):
        snapshots = model / "snapshots"
        if not snapshots.is_dir():
            continue
        weights = _newest_weights_file(snapshots)
        if weights is None:
            continue
        blobs = model / "blobs"
        size = sum(f.stat().st_size for f in blobs.glob("*") if f.is_file())
        repo_id = model.name.removeprefix("models--").replace("--", "/")
        yield repo_id, model.resolve(), size, weights


def _ollama_store_models(store: Path):
    manifests = store / "manifests"
    if not manifests.is_dir():
        return
    for manifest in sorted(p for p in manifests.rglob("*") if p.is_file()):
        rel = manifest.relative_to(manifests).parts
        if len(rel) < 3:
            continue
        host, middle, variant = rel[0], list(rel[1:-1]), rel[-1]
        if host == "registry.ollama.ai" and middle and middle[0] == "library":
            middle = middle[1:]
        prefix = "" if host == "registry.ollama.ai" else f"{host}/"
        tag = f"{prefix}{'/'.join(middle)}:{variant}"
        try:
            layers = json.loads(manifest.read_text()).get("layers", [])
        except (OSError, ValueError):
            continue
        size = sum(layer.get("size", 0) for layer in layers)
        blob = next(
            (
                store / "blobs" / layer["digest"].replace(":", "-")
                for layer in layers
                if layer.get("mediaType", "").endswith("model")
            ),
            manifest,
        )
        yield tag, size, blob


def _voice_files(directory: Path):
    if directory.is_dir():
        yield from sorted(
            p for p in directory.iterdir() if p.is_file() and not p.name.startswith(".")
        )


def downloaded_entries() -> list[dict]:
    """Every model actually on disk, across the locat store AND the tools' home caches."""
    entries: list[dict] = []

    seen_dirs: set[Path] = set()
    for hub in [Path(os.environ["HF_HOME"]) / "hub", _home_hf_hub()]:
        for repo_id, real_dir, size, weights in _hf_store_models(hub):
            if real_dir in seen_dirs:
                continue
            seen_dirs.add(real_dir)
            entries.append({"kind": "hf", "name": repo_id, "bytes": size, "path": str(weights)})

    seen_tags: set[str] = set()
    for store in [Path(config.ollama_models_dir()), _home_ollama_store()]:
        for tag, size, blob in _ollama_store_models(store):
            if tag in seen_tags:
                continue
            seen_tags.add(tag)
            entries.append({"kind": "ollama", "name": tag, "bytes": size, "path": str(blob)})

    for path in _voice_files(Path(config.kokoro_model_path()).parent):
        entries.append(
            {"kind": "kokoro", "name": path.name, "bytes": path.stat().st_size, "path": str(path)}
        )
    for path in _voice_files(Path(config.piper_download_dir())):
        entries.append(
            {"kind": "piper", "name": path.name, "bytes": path.stat().st_size, "path": str(path)}
        )
    return entries


def _human_size(size: int) -> str:
    """Binary units, labeled as such — HuggingFace and Ollama report decimal GB
    for the same files, so 22.8 GiB here is the 24.6 GB shown on a model page."""
    if size >= 2**30:
        return f"{size / 2**30:.1f} GiB"
    if size >= 2**20:
        return f"{size / 2**20:.0f} MiB"
    return f"{size / 2**10:.0f} KiB"


def _outside_store(path: Path) -> bool:
    return not _is_relative_to(path.resolve(), Path(config.model_dir()).resolve())


def _print_downloaded() -> None:
    entries = downloaded_entries()
    if not entries:
        print("no models downloaded yet (uv run python scripts/prefetch_models.py)")
        return
    any_outside = any(_outside_store(Path(e["path"])) for e in entries)
    if any_outside:
        print(f"⚠️  = outside LOCAT_MODEL_DIR ({config.model_dir()})")
        print("")
    total = sum(e["bytes"] for e in entries)
    name_width = max(len(e["name"]) for e in entries)
    for entry in sorted(entries, key=lambda e: (e["kind"], -e["bytes"])):
        flag = "   ⚠️" if _outside_store(Path(entry["path"])) else ""
        print(
            f"{entry['kind']:<8}{entry['name']:<{name_width + 2}}"
            f"{_human_size(entry['bytes']):>9}  {entry['path']}{flag}"
        )
    print(f"{'total':<8}{'':<{name_width + 2}}{_human_size(total):>9}")


def _stt_line() -> str:
    engine = config.stt_engine()
    if engine == "whisper_mlx":
        name = config.whisper_model()
        try:
            from pipecat.services.whisper.stt import MLXModel

            return f"Whisper-MLX {name} ({MLXModel[name].value})"
        except (ImportError, KeyError):
            # pipecat missing (deps not synced) or LOCAT_WHISPER_MODEL not a valid
            # enum member — still print the configured name rather than dying.
            return f"Whisper-MLX {name}"
    if engine == "faster_whisper":
        return f"faster-whisper {config.faster_whisper_model()} (CPU)"
    if engine == "moonshine":
        return f"Moonshine {config.moonshine_model()} (CPU)"
    return f"unknown engine '{engine}'"


def _tts_line() -> str:
    engine = config.tts_engine()
    if engine == "kokoro":
        return f"Kokoro {Path(config.kokoro_model_path()).name} · voice {config.kokoro_voice()}"
    if engine == "piper":
        return f"Piper · voice {config.piper_voice()}"
    return f"unknown engine '{engine}'"


def model_entries() -> list[dict]:
    """Importable form of the model lines (bot_moq sends them as `locat-config`)."""
    ollama = f"(Ollama @ {config.ollama_base_url()})"
    return [
        {"role": "STT", "model": _stt_line(), "path": _path_line(_stt_file())},
        {
            "role": "LLM",
            "model": f"{config.llm_model()} {ollama}",
            "path": _path_line(_ollama_file(config.llm_model())),
        },
        {"role": "TTS", "model": _tts_line(), "path": _path_line(_tts_file())},
        {
            "role": "EMBED",
            "model": f"{config.embed_model()} {ollama}",
            "path": _path_line(_ollama_file(config.embed_model())),
        },
    ]


def main() -> None:
    # --downloaded / -d: inventory of everything on disk instead of the configured four.
    if {"--downloaded", "-d"} & set(sys.argv[1:]):
        _print_downloaded()
        return
    # --bare: just the aligned lines, no "models:" prefix or blank lines
    # (configure.sh prints its own section header above them).
    bare = "--bare" in sys.argv[1:]
    lines = []
    for entry in model_entries():
        lines.append(f"{entry['role']:<7}{entry['model']}")
        lines.append(f"       {entry['path']}")
    if bare:
        for line in lines:
            print(f"         {line}")
        return
    print("")
    print(f"models:  {lines[0]}")
    for line in lines[1:]:
        print(f"         {line}")
    print("")


if __name__ == "__main__":
    main()
