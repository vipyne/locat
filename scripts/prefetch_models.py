#!/usr/bin/env python
"""prefetch_models.py — one-time ONLINE warm-up for EVERY default model.

The whole point of ``lofi`` is a bot that runs 100% offline. This script
downloads everything the bot needs NOW, while you still have connectivity, and
steers every cache into ``$LOFI_MODEL_DIR`` (gitignored) so all checkpoints
live next to the code. Run it once:

    uv run python scripts/prefetch_models.py

After it finishes you can turn off Wi-Fi and ``uv run bot.py`` will find every
weight locally. What it fetches:

    * Ollama LLM + embed model — pulled via ``ollama pull`` (temporary
      ``ollama serve`` is started if none is running).
    * STT — whichever engine LOFI_STT_ENGINE selects: Whisper-MLX snapshot on
      Apple Silicon, faster-whisper / Moonshine weights elsewhere.
    * Kokoro (TTS)       — ONNX model + voices bundle.
    * Silero VAD         — *bundled inside the pipecat package* (no download).
    * Smart Turn v3      — *bundled inside the pipecat package* (no download).
    * Daily bundles — the browser client's device plumbing; daily-js lazy-loads
      the call machine (and the call machine its audio processor) from
      c.daily.co, so both have to be on disk for an airgapped browser session
      (see bot_browser.py's /daily/ route).

Config knobs (env or ./.env, all optional — sensible defaults):
    LOFI_MODEL_DIR            the one directory all models go in  (default ./models)
    LOFI_LLM_MODEL            Ollama tag for the pipeline LLM     (default qwen2.5:7b)
    LOFI_EMBED_MODEL          Ollama tag for RAG embeddings       (default nomic-embed-text)
    LOFI_STT_ENGINE           whisper_mlx | faster_whisper | moonshine
    LOFI_WHISPER_MODEL        MLXModel member name                (default LARGE_V3_TURBO)
    HF_HOME                    HF cache root       (default $LOFI_MODEL_DIR/huggingface)
    LOFI_KOKORO_MODEL_PATH    Kokoro ONNX dest.   (default $LOFI_MODEL_DIR/kokoro/kokoro-v1.0.onnx)
    LOFI_KOKORO_VOICES_PATH   Kokoro voices dest. (default $LOFI_MODEL_DIR/kokoro/voices-v1.0.bin)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# --- Resolve cache locations BEFORE importing any model library, so Hugging
#     Face / MLX / Kokoro see them at import time. -------------------------------
# Importing config does all of it: loads ./.env, resolves LOFI_MODEL_DIR, and
# setdefaults HF_HOME / KOKORO_* / LOFI_PIPER_DOWNLOAD_DIR / OLLAMA_MODELS. Sharing
# that one resolver is what guarantees the prefetch writes exactly where the bot
# later reads — the whole point of the warm-up.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from loguru import logger  # noqa: E402  (import after env is set, on purpose)

OLLAMA_SERVE_TIMEOUT_SECS = 30

# Both files daily-js expects next to each other under one bundle path: the
# call machine, and the audio-processor chunk it webpack-lazy-loads relative to
# itself the moment anything starts an audio-level observer (pipecat's
# DailyMediaManager always does).
DAILY_BUNDLE_NAMES = ("call-machine-object-bundle.js", "audio-processor-bundle.js")
DAILY_BUNDLE_CDN = "https://c.daily.co/call-machine/versioned/{version}/static/"
CLIENT_LOCK_FILE = Path(__file__).resolve().parent.parent / "client" / "package-lock.json"
DAILY_LOCK_KEY = "node_modules/@daily-co/daily-js"


def daily_js_version(lock_file: Path | None = None) -> str:
    """The daily-js version npm actually installed, from client/package-lock.json.

    The bundle URL is versioned and daily-js interpolates its OWN version into
    it, so the lock file is the only source that cannot drift from what the
    built client asks for.
    """
    lock_file = lock_file or CLIENT_LOCK_FILE
    try:
        packages = json.loads(lock_file.read_text())["packages"]
        return str(packages[DAILY_LOCK_KEY]["version"])
    except (OSError, KeyError, ValueError) as exc:
        raise SystemExit(
            f"\n✖ could not read the daily-js version from {lock_file}\n"
            "  Run 'npm install' in client/, then re-run this script.\n"
        ) from exc


def daily_bundle_url(name: str) -> str:
    """CDN URL for one of the locked daily-js version's bundles."""
    return DAILY_BUNDLE_CDN.format(version=daily_js_version()) + name


def daily_bundle_path(name: str) -> Path:
    """Where a vendored bundle lives: $LOFI_MODEL_DIR/daily/<name>."""
    return Path(config.model_dir()) / "daily" / name


def ollama_tags() -> list[str]:
    """Ollama tags the bot serves: pipeline LLM, embed model, and the records
    analyst, deduped."""
    tags = [
        config.llm_model(),
        config.embed_model(),
        config.rag_llm_model(),
    ]
    return list(dict.fromkeys(tags))


def _ollama_server_up() -> bool:
    """Probe the native API's /api/tags route."""
    url = config.ollama_api_url().rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=2):
            return True
    except (urllib.error.URLError, OSError):
        return False


def prefetch_ollama_models() -> None:
    """Pull the LLM + embed model into the Ollama store (OLLAMA_MODELS).

    Reuses a running Ollama server when one is reachable; otherwise starts a
    temporary ``ollama serve`` for the pulls and stops it afterwards.
    """
    if shutil.which("ollama") is None:
        raise SystemExit(
            "\n✖ 'ollama' not found on PATH\n"
            "  Install it from https://ollama.com, then re-run this script.\n"
        )

    # ollama CLI dials OLLAMA_HOST; derive it from the same URL the bot uses.
    host = config.ollama_api_url().removeprefix("http://").removeprefix("https://")
    os.environ.setdefault("OLLAMA_HOST", host)

    server: subprocess.Popen | None = None
    if _ollama_server_up():
        logger.info("Ollama: server already running — pulling into it.")
    else:
        logger.info(f"Ollama: starting temporary server (store → {os.environ['OLLAMA_MODELS']}) ...")
        server = subprocess.Popen(
            ["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        deadline = time.monotonic() + OLLAMA_SERVE_TIMEOUT_SECS
        while not _ollama_server_up():
            if time.monotonic() > deadline or server.poll() is not None:
                raise SystemExit(
                    "\n✖ temporary 'ollama serve' did not become ready\n"
                    "  Start the server yourself and re-run:  bash scripts/run_ollama.sh\n"
                )
            time.sleep(0.5)

    try:
        for tag in ollama_tags():
            logger.info(f"Ollama: pulling '{tag}' ...")
            try:
                subprocess.run(["ollama", "pull", tag], check=True)
            except subprocess.CalledProcessError as exc:
                raise SystemExit(
                    f"\n✖ 'ollama pull {tag}' failed (exit {exc.returncode})\n"
                    "  Check the tag and your connection, then re-run this script.\n"
                ) from exc
            logger.success(f"Ollama: '{tag}' ready.")
    finally:
        if server is not None:
            server.terminate()
            server.wait(timeout=10)
            logger.info("Ollama: temporary server stopped.")


def prefetch_stt() -> None:
    """Force the configured STT engine's weights into the HF cache (HF_HOME).

    ``WhisperSTTServiceMLX`` loads lazily (its ``_load`` is a no-op) and only
    downloads on the first transcription, so for whisper_mlx we resolve the
    model's Hugging Face repo id from the ``MLXModel`` enum and pull a full
    snapshot up front. faster-whisper and Moonshine load (and download) in
    their constructors, so ``services.build_stt()`` is the warm-up.
    """
    engine = config.stt_engine()

    if engine == "whisper_mlx":
        from huggingface_hub import snapshot_download

        from pipecat.services.whisper.stt import MLXModel

        whisper_model = config.whisper_model()
        try:
            repo_id = MLXModel[whisper_model].value
        except KeyError as exc:
            valid = ", ".join(m.name for m in MLXModel)
            raise SystemExit(
                f"LOFI_WHISPER_MODEL='{whisper_model}' is not a valid MLXModel. Choose one of: {valid}"
            ) from exc

        logger.info(f"Whisper-MLX: downloading '{repo_id}' → {os.environ['HF_HOME']} ...")
        path = snapshot_download(repo_id=repo_id)
        logger.success(f"Whisper-MLX ready: {path}")
        return

    import services

    logger.info(f"STT ({engine}): downloading weights via service construction ...")
    services.build_stt()
    logger.success(f"STT ({engine}) ready.")


def prefetch_kokoro() -> None:
    """Force Kokoro's ONNX model + voices bundle onto disk under $LOFI_MODEL_DIR/kokoro.

    ``KokoroTTSService.__init__`` calls ``_ensure_model_files(model_path, voices_path)``,
    which downloads both files if they are missing, then loads them. Passing our
    repo-local paths makes the download land exactly where the bot will look.
    """
    from pipecat.services.kokoro.tts import KokoroTTSService

    model_path = config.kokoro_model_path()
    voices_path = config.kokoro_voices_path()
    logger.info(f"Kokoro: ensuring model → {model_path} and voices → {voices_path} ...")
    # Construction triggers the download + a load of the ONNX model.
    KokoroTTSService(
        settings=KokoroTTSService.Settings(voice=config.kokoro_voice()),
        model_path=model_path,
        voices_path=voices_path,
    )
    logger.success("Kokoro ready.")


def prefetch_daily_bundles() -> None:
    """Download daily-js's bundles into $LOFI_MODEL_DIR/daily.

    Both browser transports stand up a DailyMediaManager for mic/device
    plumbing, and daily-js fetches that engine from c.daily.co at device init.
    bot_browser.py serves the downloaded copies at /daily/<name> and the client
    points daily-js there, so an airgapped session makes no external request.
    """
    for name in DAILY_BUNDLE_NAMES:
        url = daily_bundle_url(name)
        dest = daily_bundle_path(name)
        dest.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"Daily: downloading {url} → {dest} ...")
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                payload = resp.read()
        except (urllib.error.URLError, OSError) as exc:
            raise SystemExit(
                f"\n✖ could not download a Daily bundle\n"
                f"  {url}\n  {exc}\n"
                "  Check your connection and re-run this script.\n"
            ) from exc

        dest.write_bytes(payload)
        logger.success(f"Daily: {name} ready ({len(payload) / 1e6:.1f} MB) → {dest}")


def verify_bundled_models() -> None:
    """Instantiate the components whose weights ship inside the pipecat package.

    Silero VAD and Smart Turn v3 do NOT download anything — their ONNX files are
    packaged and loaded via importlib.resources. We construct them anyway so the
    warm-up run also confirms they load cleanly (a fast, offline sanity check).
    """
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
    from pipecat.audio.vad.silero import SileroVADAnalyzer

    logger.info("Silero VAD: loading bundled model (no download) ...")
    SileroVADAnalyzer()
    logger.success("Silero VAD ready (bundled).")

    logger.info("Smart Turn v3: loading bundled ONNX model (no download) ...")
    LocalSmartTurnAnalyzerV3()
    logger.success("Smart Turn v3 ready (bundled).")


def main() -> None:
    models_dir = Path(config.model_dir())
    Path(os.environ["HF_HOME"]).mkdir(parents=True, exist_ok=True)
    Path(config.kokoro_model_path()).parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Prefetching models into {models_dir} (HF_HOME={os.environ['HF_HOME']})")
    prefetch_ollama_models()
    prefetch_stt()
    prefetch_kokoro()
    prefetch_daily_bundles()
    verify_bundled_models()
    logger.success("All models prefetched. You can now run the bot fully offline.")


if __name__ == "__main__":
    main()
