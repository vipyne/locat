#!/usr/bin/env python3
"""Build doctor.sh's LLM catalog from ollama.com, emitting `tag|GB|note|activeGB` rows.

stdlib + system python3 only: doctor.sh calls this before any venv is guaranteed.
Writes to stdout; doctor.sh caches the result and refreshes it weekly.

    python3 scripts/fetch_catalog.py > cache
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

LIBRARY = "https://ollama.com/library"
REGISTRY = "https://registry.ollama.ai/v2/library"
UA = {"User-Agent": "locat-doctor"}
MANIFEST = {**UA, "Accept": "application/vnd.docker.distribution.manifest.v2+json"}

# Nothing above this can run on a consumer machine; skips ~25 pointless fetches.
MAX_PARAMS_B = 150
WORKERS = 16
# Wall-clock budget. Past it we stop fetching and emit what we have rather than
# letting doctor hang on a network that accepts connections but never answers.
DEADLINE_SECS = float(os.environ.get("LOCAT_CATALOG_DEADLINE", "45"))
START = time.monotonic()


def out_of_time():
    return time.monotonic() - START > DEADLINE_SECS


def get(url, headers=UA, timeout=15):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def parse_library(html):
    """Each library entry yields (name, description, labels, params, pulls, age)."""
    out = []
    # Split on </li>, not on "Pulls" — the Updated timestamp trails the pull
    # count, so cutting at "Pulls" silently loses every model's age.
    for block in html.split("</li>"):
        m = re.search(r'href="/library/([a-zA-Z0-9._-]+)"', block)
        if not m:
            continue
        name = m.group(1)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", block))
        labels = set(re.findall(r"\b(tools|thinking|vision|embedding|cloud)\b", text))
        params = sorted(
            {float(n) for n, u in re.findall(r"\b(\d+(?:\.\d+)?)([bm])\b", text) if u == "b"},
            reverse=True,   # largest first: doctor caps variants per family
        )
        out.append((name, text, labels, params, pulls(text), age_days(text)))
    return out


def pulls(text):
    """Download count, shown as e.g. `8.7M Pulls` / `724.2K Pulls`."""
    m = re.search(r"([\d.]+)\s*([KM]?)\s*(?:&nbsp;)?\s*Pulls", text)
    if not m:
        return 0
    try:
        return float(m.group(1)) * {"M": 1_000_000, "K": 1_000, "": 1}[m.group(2)]
    except (ValueError, KeyError):
        return 0


AGE_UNITS = {"day": 1, "week": 7, "month": 30, "year": 365, "hour": 0, "minute": 0}


def age_days(text):
    """`Updated 2 weeks ago` -> 14. Unknown sorts last."""
    m = re.search(r"Updated\s*(?:&nbsp;)?\s*(?:about\s+)?(\d+)\s+(\w+?)s?\s+ago", text)
    if not m:
        return 10_000
    return int(m.group(1)) * AGE_UNITS.get(m.group(2), 365)


def keep(name, labels, params):
    # `vision` is deliberately NOT excluded: it marks multimodal, not
    # image-only, and most current chat models carry it (gemma3 does, and has
    # no `tools` label either — no label combination separates llava from a
    # general chat model, so excluding on it silently drops good ones).
    if "embedding" in labels:
        return False
    # Cloud-only models list no parameter sizes — they cannot be pulled locally,
    # and running one would defeat the point of this project.
    return bool(params)


def fmt_params(p):
    return f"{p:g}b"


def model_gb(name, tag):
    if out_of_time():
        return None
    try:
        d = json.loads(get(f"{REGISTRY}/{name}/manifests/{tag}", MANIFEST, timeout=10))
    except Exception:
        return None
    layers = [l for l in d.get("layers", []) if "model" in l.get("mediaType", "")]
    return sum(l["size"] for l in layers) / 1e9 if layers else None


def moe_active(name):
    """Map total params -> active params from tag names like `30b-a3b`."""
    if out_of_time():
        return {}
    try:
        html = get(f"https://ollama.com/library/{name}/tags", timeout=15)
    except Exception:
        return {}
    found = {}
    for total, active in re.findall(rf"{re.escape(name)}:(\d+(?:\.\d+)?)b-a(\d+(?:\.\d+)?)b", html):
        found[float(total)] = float(active)
    return found


def main():
    entries = [e for e in parse_library(get(LIBRARY)) if keep(e[0], e[2], e[3])]
    # Popularity alone buries anything new: pull counts are cumulative, so a
    # model released this month ranks below year-old ones no matter how good.
    # Interleaving the two orderings keeps the familiar names AND the new
    # arrivals near the top, and doctor only ever shows a capped slice.
    by_pulls = sorted(entries, key=lambda e: -e[4])
    by_age = sorted(entries, key=lambda e: e[5])
    entries, seen = [], set()
    for pair in zip(by_pulls, by_age):
        for e in pair:
            if e[0] not in seen:
                seen.add(e[0])
                entries.append(e)

    # Every model's tags page, not just ones whose blurb says "MoE" — several
    # (nemotron-3-nano) are MoE without saying so, and mislabelling one as dense
    # makes doctor call the fastest models in the catalog too slow.
    names = [e[0] for e in entries]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        moe_map = dict(zip(names, ex.map(moe_active, names)))

    wanted = [(e[0], p) for e in entries for p in e[3] if p <= MAX_PARAMS_B]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        sizes = list(ex.map(lambda t: model_gb(t[0], fmt_params(t[1])), wanted))

    labels_by_name = {e[0]: e[2] for e in entries}
    rows = []
    for (name, params), gb in zip(wanted, sizes):
        if not gb:
            continue
        total_gb = max(1, round(gb))
        notes = []
        active_gb = ""
        active = moe_map.get(name, {}).get(params)
        if active:
            active_gb = str(max(1, math.ceil(total_gb * active / params)))
            notes.append(f"MoE, {fmt_params(active)} active")
        # doctor.sh excludes notes matching /thinking|reasoning/ from its
        # recommended cascades; the label drives that automatically now.
        if "thinking" in labels_by_name.get(name, ()):
            notes.append("thinking")
        rows.append(f"{name}:{fmt_params(params)}|{total_gb}|{'; '.join(notes)}|{active_gb}")

    print(f"# generated {int(time.time())} {time.strftime('%Y-%m-%d')} models={len(entries)}")
    for r in rows:
        print(r)
    if not rows:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
