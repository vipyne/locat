"""The served client page: index and assets come off disk, and index.html is
no-cache so a rebuilt dist's hashed asset names reach the browser on a plain
reload."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bot_browser import CLIENT_DIST, _serve_client_dist


def make_client() -> TestClient:
    app = FastAPI()
    _serve_client_dist(app)
    return TestClient(app)


def test_index_served_with_no_cache():
    response = make_client().get("/")
    assert response.status_code == 200
    assert response.headers.get("Cache-Control") == "no-cache"


def test_assets_served_from_dist():
    asset = next(Path(CLIENT_DIST, "assets").iterdir())
    assert make_client().get(f"/assets/{asset.name}").status_code == 200
