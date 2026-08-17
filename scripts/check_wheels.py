#!/usr/bin/env python3
"""Report dependencies that have no prebuilt wheel for a target platform.

Everything here must install from a wheel: a source fallback means the machine
needs a full toolchain (LLVM for llvmlite, Rust for cryptography) just to run
`uv sync`, which is exactly the failure this repo keeps hitting on older/Intel
machines. Upstream projects drop old-arch wheels without warning, so this is a
tripwire to run when bumping pipecat — from any machine, for every platform we
support, without needing that hardware.

    python3 scripts/check_wheels.py             # all platforms
    python3 scripts/check_wheels.py intel-mac   # just one

Deliberately stdlib-only and run with the system python, not `uv run` — it has
to work on the machine where `uv sync` is what's broken.

Exits non-zero if any platform has a gap. When it flags something, the fix is
usually a marker-gated ceiling in pyproject.toml's [tool.uv] — see the
constraint-dependencies block there for the existing ones.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# label -> (uv --python-platform target, wheel-tag fragments, extra env)
#
# The OS floors matter: uv assumes a conservative minimum per target, and some
# of our deps have already moved past it. onnxruntime 1.24 ships macosx_14_0
# arm64 and manylinux_2_27+ only, so an Apple Silicon Mac needs macOS 14+ and a
# Linux box needs glibc 2.28+. Resolving against uv's older defaults reports a
# false "unsatisfiable" — so pin the floors we actually support.
PLATFORMS = {
    "intel-mac": ("x86_64-apple-darwin", ("macosx_", "x86_64"), {}),
    "arm-mac": ("aarch64-apple-darwin", ("macosx_", "arm64"), {"MACOSX_DEPLOYMENT_TARGET": "14.0"}),
    "linux-x86": ("x86_64-manylinux_2_28", ("manylinux", "x86_64"), {}),
    "windows": ("x86_64-pc-windows-msvc", ("win_amd64",), {}),
}

# Pure-Python sdists (build anywhere, no compiler) and packages we knowingly
# build from source. pyaudio needs portaudio headers, which doctor.sh checks
# for separately.
ALLOWED_SOURCE_BUILDS = {"pyaudio", "docopt"}

PYTHON_VERSION = "3.12"


def resolve(uv_platform: str, extra_env: dict[str, str]) -> list[tuple[str, str]]:
    """Resolve the full dependency tree as uv would on `uv_platform`."""
    proc = subprocess.run(
        [
            "uv", "pip", "compile", "pyproject.toml",
            "--python-platform", uv_platform,
            "--python-version", PYTHON_VERSION,
            "--no-annotate", "--no-header",
            # no -o: uv writes the resolution to stdout. ("-o -" does not mean
            # stdout here — uv creates a file literally named "-".)
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
        env={**os.environ, **extra_env},
    )
    if proc.returncode != 0:
        raise RuntimeError(f"uv pip compile failed for {uv_platform}:\n{proc.stderr}")

    packages = []
    for line in proc.stdout.splitlines():
        line = line.split("#")[0].strip()
        if "==" in line:
            name, _, version = line.partition("==")
            packages.append((name.strip().lower(), version.strip().split()[0]))
    return packages


def wheel_filenames(name: str, version: str) -> list[str]:
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as response:
        data = json.load(response)
    return [f["filename"] for f in data["urls"] if f["filename"].endswith(".whl")]


def wheel_matches(filename: str, want: tuple[str, ...]) -> bool:
    if filename.endswith("-none-any.whl"):
        return True  # pure python, works everywhere
    # cp312 = this interpreter; abi3/py3 = stable-ABI or pure-python-with-binary
    if not any(tag in filename for tag in ("cp312", "abi3", "-py3-none-", "-py2.py3-none-")):
        return False
    if want[0] == "macosx_" and "macosx_" in filename:
        # universal2 is a fat binary covering both x86_64 and arm64
        return want[1] in filename or "universal2" in filename
    return all(fragment in filename for fragment in want)


def audit(label: str, uv_platform: str, want: tuple[str, ...], extra_env: dict[str, str]) -> list[str]:
    packages = resolve(uv_platform, extra_env)

    gaps, unchecked = [], []
    for name, version in packages:
        if name in ALLOWED_SOURCE_BUILDS:
            continue
        try:
            wheels = wheel_filenames(name, version)
        except Exception as exc:  # network hiccup or yanked release
            unchecked.append(f"{name}=={version}: {exc}")
            continue
        if not any(wheel_matches(w, want) for w in wheels):
            reason = "no wheels published" if not wheels else f"no wheel tagged {'+'.join(want)}"
            gaps.append(f"{name}=={version} ({reason})")

    status = "OK" if not gaps else f"{len(gaps)} WITHOUT WHEELS"
    print(f"{label:11} {uv_platform:24} {len(packages):>3} packages — {status}")
    for gap in gaps:
        print(f"    ✗ {gap}")
    for item in unchecked:
        print(f"    ? could not check {item}")
    return gaps


def main() -> int:
    requested = sys.argv[1:] or list(PLATFORMS)
    unknown = [name for name in requested if name not in PLATFORMS]
    if unknown:
        print(f"unknown platform(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"choose from: {', '.join(PLATFORMS)}", file=sys.stderr)
        return 2

    total = 0
    for label in requested:
        uv_platform, want, extra_env = PLATFORMS[label]
        try:
            total += len(audit(label, uv_platform, want, extra_env))
        except RuntimeError as exc:
            print(f"{label:11} RESOLUTION FAILED\n{exc}", file=sys.stderr)
            total += 1

    print()
    if total:
        print(f"{total} package(s) would build from source — add a marker-gated")
        print("ceiling to [tool.uv] constraint-dependencies in pyproject.toml.")
        return 1
    print("all platforms install from wheels")
    return 0


if __name__ == "__main__":
    sys.exit(main())
