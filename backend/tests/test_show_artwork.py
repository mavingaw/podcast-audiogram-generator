"""One cover picture for the show, so uploads get a background too."""

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


def upload(client, name, content_type) -> dict:
    r = client.post("/api/media/upload", files={"file": (name, b"bytes here", content_type)})
    assert r.status_code == 200, r.text
    return r.json()["media"]


def test_nothing_chosen_means_nothing_applied(client):
    login(client)
    assert client.get("/api/settings/artwork").json() == {"media_id": None}
    ep = upload(client, "ep.mp3", "audio/mpeg")
    assert ep["artwork_media_id"] is None


def test_chosen_artwork_reaches_old_and_new_uploads(client):
    login(client)
    old = upload(client, "old.mp3", "audio/mpeg")
    art = upload(client, "cover.png", "image/png")
    r = client.put("/api/settings/artwork", json={"media_id": art["id"]})
    assert r.status_code == 200 and r.json()["applied_to"] == 1
    assert client.get("/api/settings/artwork").json()["media_id"] == art["id"]
    assert client.get(f"/api/media/{old['id']}").json()["media"]["artwork_media_id"] == art["id"]
    new = upload(client, "new.mp3", "audio/mpeg")
    assert new["artwork_media_id"] == art["id"]
    # The picture itself never gets a picture.
    assert upload(client, "another.png", "image/png")["artwork_media_id"] is None


def test_an_episode_with_its_own_artwork_keeps_it(client):
    login(client)
    own = upload(client, "own.png", "image/png")
    ep = upload(client, "ep.mp3", "audio/mpeg")
    client.patch(f"/api/media/{ep['id']}", json={"artwork_media_id": own["id"]}) if False else None
    from app.db.models import MediaAsset
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        db.get(MediaAsset, ep["id"]).artwork_media_id = own["id"]
        db.commit()
    show = upload(client, "show.png", "image/png")
    client.put("/api/settings/artwork", json={"media_id": show["id"]})
    assert client.get(f"/api/media/{ep['id']}").json()["media"]["artwork_media_id"] == own["id"]


def test_only_images_can_be_the_artwork(client):
    login(client)
    ep = upload(client, "ep.mp3", "audio/mpeg")
    assert client.put("/api/settings/artwork", json={"media_id": ep["id"]}).status_code == 400
    assert client.put("/api/settings/artwork", json={"media_id": "nope"}).status_code == 404


def test_clearing_it(client):
    login(client)
    art = upload(client, "cover.png", "image/png")
    client.put("/api/settings/artwork", json={"media_id": art["id"]})
    client.put("/api/settings/artwork", json={"media_id": None})
    assert client.get("/api/settings/artwork").json()["media_id"] is None
