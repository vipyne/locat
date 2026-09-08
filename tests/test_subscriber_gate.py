import asyncio
from types import SimpleNamespace

from bot_moq import wait_for_audio_subscriber


def make_transport(audio_out="open"):
    return SimpleNamespace(_client=SimpleNamespace(_audio_out=audio_out))


def gate(transport, ready_after: float | None, timeout: float) -> bool:
    async def run():
        client_ready = asyncio.Event()
        if ready_after == 0:
            client_ready.set()
        elif ready_after is not None:
            asyncio.get_running_loop().call_later(ready_after, client_ready.set)
        return await wait_for_audio_subscriber(transport, client_ready, timeout)

    return asyncio.run(run())


def test_passes_when_track_open_and_client_ready():
    assert gate(make_transport(), ready_after=0, timeout=1.0) is True


def test_waits_for_client_ready():
    assert gate(make_transport(), ready_after=0.1, timeout=5.0) is True


def test_waits_for_audio_track_to_open():
    async def run():
        transport = make_transport(audio_out=None)
        client_ready = asyncio.Event()
        client_ready.set()
        loop = asyncio.get_running_loop()
        loop.call_later(0.1, lambda: setattr(transport._client, "_audio_out", "open"))
        return await wait_for_audio_subscriber(transport, client_ready, timeout=5.0)

    assert asyncio.run(run()) is True


def test_times_out_without_client_ready():
    assert gate(make_transport(), ready_after=None, timeout=0.2) is False


def test_times_out_when_track_never_opens():
    assert gate(make_transport(audio_out=None), ready_after=0, timeout=0.2) is False


def test_installed_transport_still_has_the_private_attrs():
    """The gate reaches into transport._client._audio_out — fail loudly if a
    pipecat upgrade renames either attribute."""
    from pipecat.transports.moq.transport import MOQTransport, MOQTransportClient

    assert "_client" in MOQTransport.__init__.__code__.co_names
    assert "_audio_out" in MOQTransportClient.__init__.__code__.co_names
