"""Connecting a YouTube channel and posting a clip to it.

Google is never called: every request is answered by a stand-in that
records what it was asked, so the flow is checked end to end without a
network or a real channel.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services import youtube
from tests.test_api import create_test_client


@pytest.fixture()
def client(monkeypatch, tmp_path):
    with create_test_client(monkeypatch, tmp_path) as test_client:
        yield test_client


def login(client) -> None:
    client.post("/api/bootstrap", json={
        "username": "owner", "password": "Passw0rd!enough", "display_name": "Owner",
    })


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body or {}
        self.headers = headers or {}

    def json(self):
        return self._body


def fake_google(monkeypatch, calls):
    def post(url, data=None, params=None, headers=None, timeout=None):
        calls.append(("POST", url, data, params, headers))
        if url == youtube.TOKEN_URL:
            return FakeResponse(200, {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600})
        if url == youtube.UPLOAD_URL:
            return FakeResponse(200, {}, {"Location": "https://upload.example/session-1"})
        return FakeResponse(404, {"error": {"message": "unexpected"}})

    def put(url, data=None, headers=None, timeout=None):
        calls.append(("PUT", url, len(data.read()) if hasattr(data, "read") else data, None, headers))
        return FakeResponse(200, {"id": "vid123"})

    def get(url, params=None, headers=None, timeout=None):
        calls.append(("GET", url, None, params, headers))
        return FakeResponse(200, {"items": [{"id": "UC1", "snippet": {"title": "Adult-Ish"}}]})

    monkeypatch.setattr(youtube.requests, "post", post)
    monkeypatch.setattr(youtube.requests, "put", put)
    monkeypatch.setattr(youtube.requests, "get", get)


def test_not_set_up_says_so(client):
    login(client)
    assert client.get("/api/youtube/account").json() == {"configured": False, "connected": False, "channel": ""}
    r = client.get("/api/youtube/connect")
    assert r.status_code == 409 and "admin" in r.json()["detail"].lower()


def test_the_secret_is_never_echoed(client):
    login(client)
    r = client.put("/api/settings/youtube", json={"client_id": "cid.apps", "client_secret": "shh"})
    assert r.json() == {"client_id": "cid.apps", "has_secret": True}
    assert "shh" not in json.dumps(client.get("/api/settings/youtube").json())
    # Saving again with a blank secret keeps the old one.
    client.put("/api/settings/youtube", json={"client_id": "cid.apps", "client_secret": ""})
    assert client.get("/api/settings/youtube").json()["has_secret"] is True


def test_connect_then_post(client, monkeypatch):
    calls = []
    fake_google(monkeypatch, calls)
    login(client)
    client.put("/api/settings/youtube", json={"client_id": "cid.apps", "client_secret": "shh"})

    url = client.get("/api/youtube/connect").json()["url"]
    assert url.startswith(youtube.AUTH_URL) and "client_id=cid.apps" in url and "access_type=offline" in url
    state = url.split("state=")[1].split("&")[0]

    done = client.get("/api/youtube/callback", params={"code": "the-code", "state": state}, follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"].endswith("youtube=connected")
    acct = client.get("/api/youtube/account").json()
    assert acct == {"configured": True, "connected": True, "channel": "Adult-Ish"}

    project = client.post("/api/projects", json={"title": "The good bit"}).json()["project"]
    from app.api.routes import settings

    out = settings.outputs_dir / project["id"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "audiogram.mp4").write_bytes(b"\x00" * 4096)

    r = client.post(f"/api/projects/{project['id']}/post/youtube", json={"privacy": "unlisted"})
    assert r.status_code == 200, r.text
    assert r.json()["url"] == "https://youtu.be/vid123" and r.json()["privacy"] == "unlisted"
    started = [c for c in calls if c[1] == youtube.UPLOAD_URL][0]
    assert started[4]["X-Upload-Content-Length"] == "4096"
    sent = [c for c in calls if c[0] == "PUT"][0]
    assert sent[2] == 4096
    # Remembered on the project.
    scene = client.get(f"/api/projects/{project['id']}").json()["project"]["scene"]
    assert scene["posted"][0]["url"] == "https://youtu.be/vid123"


def test_a_forged_callback_is_refused(client, monkeypatch):
    calls = []
    fake_google(monkeypatch, calls)
    login(client)
    client.put("/api/settings/youtube", json={"client_id": "cid.apps", "client_secret": "shh"})
    client.get("/api/youtube/connect")
    r = client.get("/api/youtube/callback", params={"code": "x", "state": "wrong"}, follow_redirects=False)
    assert r.status_code == 303 and "youtube=failed" in r.headers["location"]
    assert client.get("/api/youtube/account").json()["connected"] is False


def test_posting_without_a_render_or_a_connection_explains(client, monkeypatch):
    login(client)
    project = client.post("/api/projects", json={"title": "Nope"}).json()["project"]
    r = client.post(f"/api/projects/{project['id']}/post/youtube", json={})
    assert r.status_code == 409


def test_privacy_defaults_to_private():
    assert youtube.PRIVACY[0] == "private"
