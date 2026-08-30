"""Your own profile: name, picture, password."""

from __future__ import annotations

import pytest

from tests.test_api import create_test_client


@pytest.fixture()
def client(monkeypatch, tmp_path):
    with create_test_client(monkeypatch, tmp_path) as test_client:
        yield test_client


def login(client) -> None:
    client.post("/api/bootstrap", json={
        "username": "owner", "password": "Passw0rd!enough", "display_name": "Owner",
    })


def test_display_name_and_avatar(client):
    login(client)
    me = client.get("/api/me").json()["user"]
    assert me["display_name"] == "" and me["avatar_media_id"] is None
    r = client.patch("/api/me", json={"display_name": "  Mujin  "})
    assert r.json()["user"]["display_name"] == "Mujin"

    art = client.post("/api/media/upload", files={"file": ("me.png", b"png bytes", "image/png")}).json()["media"]
    r = client.patch("/api/me", json={"avatar_media_id": art["id"]})
    assert r.json()["user"]["avatar_media_id"] == art["id"]
    assert client.get("/api/session").json()["user"]["avatar_media_id"] == art["id"]
    r = client.patch("/api/me", json={"clear_avatar": True})
    assert r.json()["user"]["avatar_media_id"] is None
    # Not an image -> refused.
    ep = client.post("/api/media/upload", files={"file": ("ep.mp3", b"mp3", "audio/mpeg")}).json()["media"]
    assert client.patch("/api/me", json={"avatar_media_id": ep["id"]}).status_code == 400


def test_password_change_needs_the_old_one(client):
    login(client)
    assert client.post("/api/me/password", json={"current": "wrong", "new": "long-enough-pass"}).status_code == 403
    assert client.post("/api/me/password", json={"current": "Passw0rd!enough", "new": "short"}).status_code == 400
    assert client.post("/api/me/password", json={"current": "Passw0rd!enough", "new": "brand-new-secret"}).status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": "owner", "password": "Passw0rd!enough"}).status_code in (401, 403)
    assert client.post("/api/auth/login", json={"username": "owner", "password": "brand-new-secret"}).status_code == 200
