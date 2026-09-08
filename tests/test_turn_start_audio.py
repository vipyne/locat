"""Guards for the two fixes that stop bot turns from starting clipped.

The browser plays bot audio through a ring buffer whose safe variant
(SharedArrayBuffer, one-second capacity) only exists on cross-origin
isolated pages, and whose overflow drops the oldest unplayed samples.
So the served page must carry COOP/COEP headers, and the bot must never
write further ahead of real time than that ring can hold.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bot_moq import CLIENT_DIST, _serve_client_dist, transport_params

BROWSER_AUDIO_RING_MS = 1000

ISOLATION_HEADERS = {
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Embedder-Policy": "require-corp",
}


def make_client() -> TestClient:
    app = FastAPI()
    _serve_client_dist(app)
    return TestClient(app)


def assert_isolated(response):
    assert response.status_code == 200
    for name, value in ISOLATION_HEADERS.items():
        assert response.headers.get(name) == value


def test_index_is_cross_origin_isolated():
    assert_isolated(make_client().get("/"))


def test_assets_are_cross_origin_isolated():
    asset = next(Path(CLIENT_DIST, "assets").iterdir())
    assert_isolated(make_client().get(f"/assets/{asset.name}"))


def test_audio_ahead_cap_fits_browser_ring():
    params = transport_params["moq"]()
    assert params.audio_out_max_buffer_ms == 500
    assert params.audio_out_max_buffer_ms < BROWSER_AUDIO_RING_MS


def test_audio_ahead_cap_is_configurable(monkeypatch):
    monkeypatch.setenv("LOCAT_MOQ_AUDIO_AHEAD_MS", "800")
    assert transport_params["moq"]().audio_out_max_buffer_ms == 800
