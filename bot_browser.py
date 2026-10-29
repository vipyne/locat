"""Browser voice bot — same brain, echo cancellation free from the browser's getUserMedia.

Serves the lofi client (client/dist/) and offers three transports. The client
picks by session mode: WebSocket for offline (pure TCP — connects with wi-fi
off, and the only path that crosses Windows↔WSL2), SmallWebRTC for online
(needs `uv sync --extra webrtc`; falls back to WebSocket after a failed
connect).

Run:
    ./lofi.sh start
    # opens http://localhost:7860 (the lofi client from client/dist/) — allow the mic, Connect.

Reuses the shared pipeline from pipeline.py (VADProcessor + Whisper/Ollama/Kokoro);
only transport construction and per-transport event-handler shapes live here.
"""

# config FIRST — sets HF_HOME / Kokoro cache paths before any pipecat/HF import (see config.py).
import config

import asyncio
import json
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import BaseTransport
from pipecat.workers.runner import WorkerRunner

import store
from bot import (
    _configure_logging,
    _ollama_models_to_check,
    _preflight_llm,
    _preflight_rag,
    _preflight_stt,
)
from pipeline import OFFLINE_MODE, ONLINE_MODE, build_pipeline, public_rag_summary
from scripts.prefetch_models import (
    DAILY_BUNDLE_NAMES,
    daily_bundle_path,
    daily_bundle_url,
)
from scripts.print_models import model_entries

# Shell env wins over .env (matches config.py and lofi.sh).
load_dotenv()

CONFIG_MESSAGE_TYPE = "lofi-config"

CLIENT_DIST = Path(__file__).resolve().parent / "client" / "dist"


def _serve_client_dist(app) -> None:
    """Register the lofi frontend on the dev runner's FastAPI app.

    The runner has no option for a custom static dir (its `_setup_frontend_routes`
    hardcodes the pipecat-ai-prebuilt UI, now uninstalled — it logs one startup
    error about that and mounts nothing). Routes registered before `main()` win.

    """
    from fastapi import HTTPException
    from fastapi.responses import FileResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=CLIENT_DIST / "assets"), name="client-assets")

    @app.get("/", include_in_schema=False)
    async def client_index():
        # no-cache: revalidate index.html on every load so a rebuilt dist's
        # hashed asset names reach the browser on a plain reload.
        return FileResponse(
            CLIENT_DIST / "index.html", headers={"Cache-Control": "no-cache"}
        )

    @app.get("/api/lofi-mode", include_in_schema=False)
    async def lofi_mode():
        # Which sessions this process serves; the client greys out the
        # unavailable option in its mode picker.
        return {"mode": config.serve_mode()}

    @app.get("/daily/{name}", include_in_schema=False)
    async def daily_bundle(name: str):
        # Both browser transports stand up a DailyMediaManager, and daily-js
        # lazy-loads its call machine at device init (which lazy-loads the
        # audio processor). main.ts points daily-js at this directory; serve
        # the prefetched copy, else send it to the versioned CDN so an online
        # session works without the prefetch.
        if name not in DAILY_BUNDLE_NAMES:
            raise HTTPException(status_code=404)
        bundle = daily_bundle_path(name)
        if bundle.is_file():
            return FileResponse(bundle, media_type="text/javascript")
        logger.warning(
            f"Daily bundle not prefetched ({bundle}) — redirecting to the CDN. "
            "Airgapped sessions need: uv run python scripts/prefetch_models.py"
        )
        return RedirectResponse(daily_bundle_url(name), status_code=302)




def _preflight_local() -> None:
    """Run the local-stack preflights, unless this process is online-only.

    An online-only process speaks to Deepgram/OpenAI/Cartesia, so Ollama,
    the Whisper weights, and the embed model are all irrelevant to it.
    """
    if config.online_only():
        logger.info("online-only process — skipping the local LLM/STT/RAG preflights")
        return
    _preflight_llm(config.ollama_base_url())
    _preflight_stt()
    _preflight_rag()


def _requested_mode(runner_args) -> str | None:
    """The mode the connect request asked for, lowercased, or None.

    WebSocket sessions carry it as `?mode=` on the /ws-client URL; SmallWebRTC
    sessions carry it as `requestData: {"mode": …}` in the /api/offer body
    (query params on /api/offer never reach the bot). Serve-mode sessions
    carry no per-connection request.
    """
    kind = _transport_kind(runner_args)
    if kind == "websocket":
        raw = runner_args.websocket.query_params.get("mode")
    elif kind == "webrtc":
        body = runner_args.body
        raw = body.get("mode") if isinstance(body, dict) else None
    else:
        raw = None
    return raw.strip().lower() if isinstance(raw, str) and raw.strip() else None


def session_mode(runner_args) -> str:
    """Which stack this connection runs on: `"online"` or `"offline"`.

    Offline is the default and the fallback for anything unrecognized. Online
    is refused up front when any of the three keys is missing, naming them all
    at once; in an `--online-only` process the refusal runs the other way and
    a bare connect defaults to online. In an `--offline-only` process an
    online connect is refused.
    """
    requested = _requested_mode(runner_args)
    if requested == ONLINE_MODE:
        mode = ONLINE_MODE
    elif requested == OFFLINE_MODE:
        mode = OFFLINE_MODE
    else:
        mode = ONLINE_MODE if config.online_only() else OFFLINE_MODE

    if mode == OFFLINE_MODE and config.online_only():
        raise RuntimeError(
            "this bot was started with --online-only — the local stack is not "
            "ready, so an offline session cannot run. Pick ☁️ online on the "
            "page, or restart without --online-only."
        )
    if mode == ONLINE_MODE and config.offline_only():
        raise RuntimeError(
            "this bot was started with --offline-only — an online session is "
            "refused. Pick 🔒 offline on the page, or restart without "
            "--offline-only."
        )
    if mode == ONLINE_MODE:
        missing = config.missing_online_keys()
        if missing:
            raise RuntimeError(
                "online session needs cloud credentials — missing from .env: "
                f"{', '.join(missing)}. Add them and reconnect, or pick "
                "🔌 offline on the page."
            )
    logger.info(f"session mode: {mode}")
    return mode


PREFETCH_CMD = "uv run python scripts/prefetch_models.py"
RUN_OLLAMA_CMD = "bash scripts/run_ollama.sh"


def _ollama_model_ids(base_url: str) -> set[str]:
    """Tags the Ollama server at `base_url` serves right now."""
    url = base_url.rstrip("/") + "/models"
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 (localhost)
        return {str(m.get("id", "")) for m in json.load(resp).get("data", [])}


def vet_offline_session() -> None:
    """Refuse an offline connect the local stack can't serve, naming the fix.

    Mirrors online's missing-keys refusal: the browser shows the error instead
    of a bot that connects, listens, and never answers. Every missing model is
    named in one message so a user pulls them in one pass.
    """
    base_url = config.ollama_base_url()
    wanted = _ollama_models_to_check()
    if not wanted:
        return
    try:
        available = _ollama_model_ids(base_url)
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(
            f"offline session needs the local LLM server — nothing answers at {base_url}. "
            f"Start it:  {RUN_OLLAMA_CMD}  (or: ollama serve), then reconnect. "
            f"Or pick ☁️ online on the page. Details: {exc}"
        ) from exc

    # Ollama reports tags like 'llama3:latest'; match the bare name too.
    missing = [
        tag
        for tag in wanted
        if not any(tag == m or tag.split(":")[0] == m.split(":")[0] for m in available)
    ]
    if missing:
        raise RuntimeError(
            "offline session needs local models Ollama doesn't have: "
            f"{', '.join(missing)}. Pull them:  {PREFETCH_CMD}  — or, if you pulled "
            f"them into a different Ollama, point lofi at it:  {RUN_OLLAMA_CMD}. "
            "Or pick ☁️ online on the page."
        )
    logger.info(f"offline session OK: {', '.join(wanted)} available at {base_url}")


def _preflight_startup() -> None:
    """Run the local-stack preflights, logging a failure instead of exiting.

    The server must boot even with no brain: the page is where a user reads
    what is missing, and online sessions need no local model at all.
    """
    try:
        _preflight_local()
    except SystemExit as exc:
        logger.warning(
            f"local stack not ready — offline sessions will be refused at connect:{exc}"
        )


def _lofi_config_message(mode: str = OFFLINE_MODE) -> dict:
    rag = (
        public_rag_summary()
        if mode == ONLINE_MODE
        else store.stats_summary(Path(config.rag_db_path()), config.rag_data_dir())
    )
    return {
        "type": CONFIG_MESSAGE_TYPE,
        "mode": mode,
        "models": model_entries(mode),
        "ollama_host": config.ollama_base_url(),
        "ollama_started_by_lofi": (Path(config.state_dir()) / "ollama.pid").is_file(),
        "architecture": config.architecture(),
        "rag": rag,
    }


def build_transport_params() -> dict:
    """Transports the runner offers via /start: SmallWebRTC when aiortc is
    installed (uv sync --extra webrtc) — what online sessions pick; WebSocket
    always — what offline sessions pick, and webrtc's fallback."""
    params = {}

    # Plain WebSocket (pure TCP): crosses any boundary that forwards TCP,
    # including Windows↔WSL2 where both QUIC and WebRTC's UDP media (and
    # aiortc's no-ICE-TCP) are dead ends.
    def websocket_params():
        from pipecat.frames.frames import InterruptionFrame, TextFrame
        from pipecat.serializers.protobuf import ProtobufFrameSerializer
        from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams

        class JsClientProtobufSerializer(ProtobufFrameSerializer):
            """The JS websocket client parses only "audio" and "message" frame
            kinds — anything else logs "Unknown frame kind" in the browser, so
            skip text/transcription/interruption at the source. (Transcripts
            and bot text reach the client as RTVI "message" frames.)"""

            async def serialize(self, frame):
                # TranscriptionFrame subclasses TextFrame.
                if isinstance(frame, (TextFrame, InterruptionFrame)):
                    return None
                return await super().serialize(frame)

        return FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=JsClientProtobufSerializer(),
        )

    params["websocket"] = websocket_params

    try:
        import aiortc  # noqa: F401
    except ImportError:
        logger.info("webrtc transport not offered — enable with: uv sync --extra webrtc")
        return params
    from pipecat.transports.base_transport import TransportParams

    params["webrtc"] = lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    )
    return params


transport_params = build_transport_params()


GREETING_SETTLE_SECS = 0.5


def _transport_kind(runner_args) -> str:
    """Which transport a runner-args object selects: /api/offer hands
    SmallWebRTCRunnerArguments to bot(), /ws-client hands
    WebSocketRunnerArguments."""
    name = type(runner_args).__name__
    return "webrtc" if name == "SmallWebRTCRunnerArguments" else "websocket"


def _greeting(mode: str) -> str:
    """Opening line for the session's mode."""
    return config.online_greeting() if mode == ONLINE_MODE else config.offline_greeting()


def _register_webrtc_handlers(transport, worker, mode: str = OFFLINE_MODE) -> None:
    # SmallWebRTC and FastAPIWebsocket handlers receive (transport, client).
    # Audio flows as soon as the connection is up — no subscriber gate needed.
    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client):
        await asyncio.sleep(GREETING_SETTLE_SECS)
        logger.info("WebRTC client connected — greeting")
        await worker.queue_frames([TTSSpeakFrame(_greeting(mode))])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        logger.info("WebRTC client disconnected — ending session")
        await worker.cancel()


async def run_bot(
    transport: BaseTransport, runner_args: RunnerArguments, mode: str = OFFLINE_MODE
) -> None:
    """Assemble and run the pipeline for one connected browser client."""
    built = build_pipeline(transport, mode=mode)
    worker = PipelineWorker(built.pipeline, params=PipelineParams())

    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        logger.info("RTVI client ready — sending lofi-config")
        await rtvi.send_server_message(_lofi_config_message(mode))

    _register_webrtc_handlers(transport, worker, mode)

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    try:
        await runner.add_workers(worker)
        await runner.run()
    finally:
        disconnect = getattr(transport, "disconnect", None)
        if disconnect is not None:
            await disconnect()


async def bot(runner_args: RunnerArguments) -> None:
    """Entry point the Pipecat dev runner discovers and calls per connection."""
    # Resolve (and vet) the mode before any transport exists, so a refused
    # session never leaves a half-open peer connection behind.
    mode = session_mode(runner_args)
    if mode == OFFLINE_MODE:
        vet_offline_session()
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args, mode=mode)


if __name__ == "__main__":
    _configure_logging()
    _preflight_startup()

    from pipecat.runner.run import app, main

    _serve_client_dist(app)
    main()
