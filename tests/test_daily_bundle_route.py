"""The /daily/ bundle route — the one thing that made airgapped browser
sessions hang at "initializing".

daily-js lazy-loads its call machine from c.daily.co at device init (and the
call machine webpack-lazy-loads its audio processor next to itself), and both
the websocket and small-webrtc transports stand up a DailyMediaManager. The
server serves a prefetched bundle same-origin when it is on disk and redirects
to the versioned CDN URL when it is not (online works unprefetched).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import bot_browser
from scripts.prefetch_models import DAILY_BUNDLE_NAMES, daily_bundle_url


@pytest.fixture
def client():
    app = FastAPI()
    bot_browser._serve_client_dist(app)
    return TestClient(app)


@pytest.mark.parametrize("name", DAILY_BUNDLE_NAMES)
def test_prefetched_bundle_served_same_origin(client, monkeypatch, tmp_path, name):
    (tmp_path / name).write_text(f"// pretend {name}\n")
    monkeypatch.setattr(bot_browser, "daily_bundle_path", lambda n: tmp_path / n)

    resp = client.get(f"/daily/{name}")

    assert resp.status_code == 200
    assert resp.text == f"// pretend {name}\n"
    assert "javascript" in resp.headers["content-type"]


@pytest.mark.parametrize("name", DAILY_BUNDLE_NAMES)
def test_missing_bundle_redirects_to_versioned_cdn(client, monkeypatch, tmp_path, name):
    monkeypatch.setattr(bot_browser, "daily_bundle_path", lambda n: tmp_path / n)

    resp = client.get(f"/daily/{name}", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == daily_bundle_url(name)


def test_audio_processor_cache_buster_query_still_resolves(client, monkeypatch, tmp_path):
    """The call machine asks for `audio-processor-bundle.js?<hash>`."""
    name = "audio-processor-bundle.js"
    (tmp_path / name).write_text("// pretend processor\n")
    monkeypatch.setattr(bot_browser, "daily_bundle_path", lambda n: tmp_path / n)

    resp = client.get(f"/daily/{name}?92041c35ee1e3cf9b55b")

    assert resp.status_code == 200
    assert resp.text == "// pretend processor\n"


def test_unknown_name_is_not_served(client):
    assert client.get("/daily/anything-else.js").status_code == 404
