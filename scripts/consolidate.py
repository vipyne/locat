"""Adopt models from default HF/Ollama locations into LOCAT_MODEL_DIR via symlinks.

`./locat.sh consolidate` makes every model visible AND loadable from one place:

- HuggingFace: per-model. A model in the external cache (~/.cache/huggingface)
  that locat's store lacks gets a symlink inside models/huggingface/hub/, so
  loaders resolve it through LOCAT_MODEL_DIR. A broken partial in locat's store
  (leftover *.incomplete download stubs) is replaced by a link to a complete
  external copy. A complete copy locat owns is never touched.
- Ollama: whole-store. Models share content-addressed blobs, so adoption links
  models/ollama -> ~/.ollama/models when locat's store is empty and the
  external one is not. Both populated = a conflict reported for a human.

External stores are never modified. The only writes are symlinks in the locat
store and removal of locat-owned 0-byte *.incomplete stubs. Idempotent.
"""

import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Action:
    kind: str
    path: Path
    target: Path | None = None

    def describe(self) -> str:
        arrow = f" -> {self.target}" if self.target else ""
        return f"{self.kind:20} {self.path}{arrow}"


def _has_dangling_snapshot_links(model: Path) -> bool:
    for link in (model / "snapshots").rglob("*"):
        if link.is_symlink() and not link.exists():
            return True
    return False


def _hf_model_is_broken(model: Path) -> bool:
    return bool(list((model / "blobs").glob("*.incomplete"))) or _has_dangling_snapshot_links(
        model
    )


def _hf_model_is_usable(model: Path) -> bool:
    snapshots = model / "snapshots"
    has_files = snapshots.is_dir() and any(p for p in snapshots.rglob("*") if not p.is_dir())
    return has_files and not _has_dangling_snapshot_links(model)


def _ollama_has_models(store: Path) -> bool:
    manifests = store / "manifests"
    return manifests.is_dir() and any(p.is_file() for p in manifests.rglob("*"))


def _hf_actions(locat_hub: Path, external_hub: Path, dry_run: bool) -> list[Action]:
    actions: list[Action] = []
    if not external_hub.is_dir():
        return actions
    for external in sorted(external_hub.glob("models--*")):
        ours = locat_hub / external.name
        if ours.is_symlink():
            actions.append(Action("hf-borrowed", ours, ours.resolve()))
        elif not ours.exists():
            actions.append(Action("hf-borrow", ours, external))
            if not dry_run:
                locat_hub.mkdir(parents=True, exist_ok=True)
                ours.symlink_to(external)
        elif _hf_model_is_broken(ours) and _hf_model_is_usable(external):
            actions.append(Action("hf-replace-partial", ours, external))
            if not dry_run:
                shutil.rmtree(ours)
                ours.symlink_to(external)
        else:
            actions.append(Action("hf-owned", ours))
    return actions


def _ollama_actions(
    locat_store: Path, external: Path | None, in_use: bool, dry_run: bool
) -> list[Action]:
    if external is None or not _ollama_has_models(external):
        return [Action("ollama-none", locat_store)]
    if locat_store.is_symlink():
        return [Action("ollama-borrowed", locat_store, locat_store.resolve())]
    if in_use:
        return [Action("ollama-in-use", locat_store, external)]
    if _ollama_has_models(locat_store):
        return [Action("ollama-conflict", locat_store, external)]
    if not dry_run:
        if locat_store.is_dir():
            shutil.rmtree(locat_store)
        locat_store.symlink_to(external)
    return [Action("ollama-borrow", locat_store, external)]


def consolidate(
    *,
    model_dir: Path,
    external_hf_hub: Path,
    external_ollama: Path | None,
    ollama_store_in_use: bool,
    dry_run: bool,
) -> list[Action]:
    locat_hub = model_dir / "huggingface" / "hub"
    actions = _hf_actions(locat_hub, external_hf_hub, dry_run)
    actions += _ollama_actions(
        model_dir / "ollama", external_ollama, ollama_store_in_use, dry_run
    )
    return actions


def _default_external_hf_hub(model_dir: Path) -> Path:
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    hub = cache_home / "huggingface" / "hub"
    if hub.resolve() == (model_dir / "huggingface" / "hub").resolve():
        return Path("/nonexistent")
    return hub


def _default_external_ollama(model_dir: Path) -> Path | None:
    home_store = Path.home() / ".ollama" / "models"
    candidate = Path(os.environ.get("OLLAMA_MODELS") or home_store)
    if not candidate.is_dir() or candidate.resolve() == (model_dir / "ollama").resolve():
        candidate = home_store
    return candidate if candidate.is_dir() else None


def _ollama_server_up() -> bool:
    import urllib.request

    host = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
    try:
        urllib.request.urlopen(f"http://{host}/api/tags", timeout=2)
        return True
    except OSError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--dry-run", action="store_true")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import config

    model_dir = Path(config.model_dir())
    actions = consolidate(
        model_dir=model_dir,
        external_hf_hub=_default_external_hf_hub(model_dir),
        external_ollama=_default_external_ollama(model_dir),
        ollama_store_in_use=_ollama_server_up(),
        dry_run=args.dry_run,
    )

    prefix = "would " if args.dry_run else ""
    for action in actions:
        print(f"{prefix}{action.describe()}")
    if any(a.kind == "ollama-in-use" for a in actions):
        print(
            "\nollama server is running — stop it (./locat.sh stop or your own instance)\n"
            "and re-run ./locat.sh consolidate to adopt its store.",
            file=sys.stderr,
        )
    if any(a.kind == "ollama-conflict" for a in actions):
        print(
            "\nboth ollama stores contain models — locat will not merge them.\n"
            "keep using the locat store, or empty it and re-run to borrow the external one.",
            file=sys.stderr,
        )
    print(f"\nbrowse everything: ls -al {model_dir}")


if __name__ == "__main__":
    main()
