"""The social connections framework, with every platform stubbed."""

from __future__ import annotations

import json

import pytest

from app.services import social
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
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


def test_every_provider_is_listed_with_its_note(client):
    login(client)
    rows = client.get("/api/settings/social").json()["providers"]
    assert {r["key"] for r in rows} == {"meta", "tiktok", "linkedin", "pinterest", "x"}
    assert all(r["note"] for r in rows)
    accounts = client.get("/api/social/accounts").json()["accounts"]
    assert accounts[0]["key"] == "youtube"
    assert all(a["configured"] is False for a in accounts)


def test_keys_drop_in_and_the_secret_never_echoes(client):
    login(client)
    r = client.put("/api/settings/social", json={"provider": "meta", "client_id": "fb-app", "client_secret": "shh"})
    assert r.json() == {"provider": "meta", "client_id": "fb-app", "has_secret": True}
    assert "shh" not in json.dumps(client.get("/api/settings/social").json())
    # Blank secret keeps the old one; blank id turns the platform off.
    client.put("/api/settings/social", json={"provider": "meta", "client_id": "fb-app", "client_secret": ""})
    assert [p for p in client.get("/api/settings/social").json()["providers"] if p["key"] == "meta"][0]["has_secret"] is True
    client.put("/api/settings/social", json={"provider": "meta", "client_id": "", "client_secret": ""})
    row = [p for p in client.get("/api/settings/social").json()["providers"] if p["key"] == "meta"][0]
    assert row["has_secret"] is False
    assert client.put("/api/settings/social", json={"provider": "myspace", "client_id": "x"}).status_code == 404


def test_connect_needs_keys_first(client):
    login(client)
    r = client.get("/api/social/meta/connect")
    assert r.status_code == 409 and "admin" in r.json()["detail"].lower()


def test_meta_connect_and_post(client, monkeypatch):
    calls = []

    def post(url, data=None, params=None, headers=None, files=None, json=None, timeout=None):
        calls.append(("POST", url))
        if url == social.PROVIDERS["meta"].token_url:
            return FakeResponse(200, {"access_token": "tok", "expires_in": 3600})
        if url.endswith("/videos"):
            return FakeResponse(200, {"id": "fbvid"})
        if url.endswith("/media"):
            return FakeResponse(200, {"id": "container", "uri": "https://rupload.example/1"})
        if url == "https://rupload.example/1":
            return FakeResponse(200, {})
        if url.endswith("/media_publish"):
            return FakeResponse(200, {"id": "igvid"})
        return FakeResponse(404, {"error": {"message": "unexpected " + url}})

    def get(url, params=None, headers=None, timeout=None):
        calls.append(("GET", url))
        if url.endswith("/me/accounts"):
            return FakeResponse(200, {"data": [{
                "id": "page1", "name": "Adult-Ish", "access_token": "pagetok",
                "instagram_business_account": {"id": "ig1", "username": "adultish"},
            }]})
        return FakeResponse(404, {})

    monkeypatch.setattr(social.requests, "post", post)
    monkeypatch.setattr(social.requests, "get", get)
    login(client)
    client.put("/api/settings/social", json={"provider": "meta", "client_id": "fb-app", "client_secret": "shh"})

    url = client.get("/api/social/meta/connect").json()["url"]
    assert url.startswith(social.PROVIDERS["meta"].auth_url) and "client_id=fb-app" in url
    state = url.split("state=")[1].split("&")[0]
    done = client.get("/api/social/meta/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert done.status_code == 303 and "result=connected" in done.headers["location"]
    acct = [a for a in client.get("/api/social/accounts").json()["accounts"] if a["key"] == "meta"][0]
    assert acct == {"key": "meta", "label": "Facebook + Instagram", "posts": acct["posts"],
                    "configured": True, "connected": True, "name": "Adult-Ish"}

    project = client.post("/api/projects", json={"title": "The good bit"}).json()["project"]
    from app.api.routes import settings

    out = settings.outputs_dir / project["id"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "audiogram.mp4").write_bytes(b"\x00" * 2048)
    r = client.post(f"/api/projects/{project['id']}/post/meta", json={})
    assert r.status_code == 200, r.text
    assert "Adult-Ish" in r.json()["detail"] and "Reel" in r.json()["detail"]
    scene = client.get(f"/api/projects/{project['id']}").json()["project"]["scene"]
    assert scene["posted"][0]["platform"] == "meta"


def test_a_forged_state_is_refused(client, monkeypatch):
    monkeypatch.setattr(social.requests, "post", lambda *a, **k: FakeResponse(200, {"access_token": "t"}))
    login(client)
    client.put("/api/settings/social", json={"provider": "linkedin", "client_id": "li", "client_secret": "s"})
    client.get("/api/social/linkedin/connect")
    r = client.get("/api/social/linkedin/callback", params={"code": "c", "state": "wrong"}, follow_redirects=False)
    assert r.status_code == 303 and "result=failed" in r.headers["location"]


def test_x_posts_the_share_link(client, monkeypatch):
    def post(url, data=None, headers=None, json=None, timeout=None):
        if url == social.PROVIDERS["x"].token_url:
            return FakeResponse(200, {"access_token": "tok", "expires_in": 3600})
        if url.endswith("/2/tweets"):
            assert "/s/" in json["text"]
            return FakeResponse(201, {"data": {"id": "199"}})
        return FakeResponse(404, {})

    monkeypatch.setattr(social.requests, "post", post)
    monkeypatch.setattr(social.requests, "get", lambda *a, **k: FakeResponse(200, {"data": {"username": "mavin"}}))
    login(client)
    client.put("/api/settings/social", json={"provider": "x", "client_id": "xapp", "client_secret": "s"})
    url = client.get("/api/social/x/connect").json()["url"]
    state = url.split("state=")[1].split("&")[0]
    client.get("/api/social/x/callback", params={"code": "c", "state": state}, follow_redirects=False)

    project = client.post("/api/projects", json={"title": "Linkable"}).json()["project"]
    from app.api.routes import settings

    out = settings.outputs_dir / project["id"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "audiogram.mp4").write_bytes(b"\x00" * 2048)
    r = client.post(f"/api/projects/{project['id']}/post/x", json={})
    assert r.status_code == 200, r.text
    assert r.json()["url"].startswith("https://x.com/mavin/status/")
