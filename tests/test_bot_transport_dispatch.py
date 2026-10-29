"""Tests for bot_browser's per-transport session wiring: SmallWebRTC greets
on client-connected and ends the session on disconnect."""

import asyncio

import bot_browser


class SmallWebRTCRunnerArguments:  # same class NAME the runner uses
    pass


class WebSocketRunnerArguments:  # same class NAME the runner uses
    pass


class RunnerArguments:
    pass


def test_transport_kind_from_runner_args():
    assert bot_browser._transport_kind(SmallWebRTCRunnerArguments()) == "webrtc"
    assert bot_browser._transport_kind(WebSocketRunnerArguments()) == "websocket"


def test_websocket_transport_params_use_protobuf_serializer():
    from pipecat.serializers.protobuf import ProtobufFrameSerializer

    params = bot_browser.build_transport_params()
    assert "websocket" in params
    ws = params["websocket"]()
    assert ws.audio_in_enabled is True
    assert ws.audio_out_enabled is True
    assert ws.add_wav_header is False
    assert isinstance(ws.serializer, ProtobufFrameSerializer)


def test_websocket_serializer_skips_kinds_the_js_client_cannot_parse():
    # @pipecat-ai/websocket-transport deserializes only "audio" and "message"
    # oneofKinds — anything else throws "Unknown frame kind" in the browser.
    from pipecat.frames.frames import (
        InterruptionFrame,
        OutputAudioRawFrame,
        TextFrame,
        TranscriptionFrame,
    )

    serializer = bot_browser.build_transport_params()["websocket"]().serializer

    async def run(frame):
        return await serializer.serialize(frame)

    assert asyncio.run(run(TextFrame(text="hi"))) is None
    assert asyncio.run(run(TranscriptionFrame(text="hi", user_id="u", timestamp="t"))) is None
    assert asyncio.run(run(InterruptionFrame())) is None
    audio = asyncio.run(
        run(OutputAudioRawFrame(audio=b"\x00\x00", sample_rate=16000, num_channels=1))
    )
    assert audio is not None


class FakeTransport:
    def __init__(self):
        self.handlers = {}

    def event_handler(self, name):
        def register(fn):
            self.handlers[name] = fn
            return fn

        return register


class FakeWorker:
    def __init__(self):
        self.queued = []
        self.cancelled = False

    async def queue_frames(self, frames):
        self.queued.extend(frames)

    async def cancel(self):
        self.cancelled = True


def test_webrtc_handlers_greet_and_end_session(monkeypatch):
    monkeypatch.setattr(bot_browser, "GREETING_SETTLE_SECS", 0)
    transport = FakeTransport()
    worker = FakeWorker()
    bot_browser._register_webrtc_handlers(transport, worker)

    assert set(transport.handlers) >= {"on_client_connected", "on_client_disconnected"}
    asyncio.run(transport.handlers["on_client_connected"](transport, object()))
    assert len(worker.queued) == 1
    asyncio.run(transport.handlers["on_client_disconnected"](transport, object()))
    assert worker.cancelled
