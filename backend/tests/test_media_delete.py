"""Removing a file from the library.

Until this existed the library only grew: a mistaken upload, a duplicate from a
retried upload, a test file — all permanent, all still on the disk. The live
install had accumulated seventy entries with no way to remove any of them.

The risk in a delete is what it takes with it, so most of this is about what
must survive: the clips someone already made, and everyone else's files.
"""

from __future__ import annotations

import pytest

from tests.test_api import create_test_client, register_second_user


@pytest.fixture()
def client(monkeypatch, tmp_path):
    with create_test_client(monkeypatch, tmp_path) as test_client:
        yield test_client


def login(client) -> None:
    client.post("/api/bootstrap", json={
        "username": "owner", "password": "Passw0rd!enough", "display_name": "Owner",
    })


def upload(client, name="episode.mp3") -> dict:
    response = client.post(
        "/api/media/upload",
        files={"file": (name, b"audio bytes here", "audio/mpeg")},
    )
    assert response.status_code == 200, response.text
    return response.json()["media"]


def test_a_deleted_file_leaves_the_library(client):
    login(client)
    media = upload(client)
    assert client.delete(f"/api/media/{media['id']}").status_code == 200
    assert client.get("/api/media").json()["media"] == []


def test_the_bytes_leave_the_disk(client):
    """A row-only delete would free nothing, which is half the point."""
    login(client)
    media = upload(client)

    from app.core.config import settings
    from app.db.models import MediaAsset
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        stored = db.get(MediaAsset, media["id"]).stored_name
    path = settings.uploads_dir / stored
    assert path.exists()

    client.delete(f"/api/media/{media['id']}")
    assert not path.exists()


def test_clips_made_from_it_survive(client):
    """Deleting a source must not silently destroy the work made from it."""
    login(client)
    media = upload(client)
    project = client.post(
        "/api/projects", json={"title": "A clip", "media_id": media["id"]}
    ).json()["project"]

    response = client.delete(f"/api/media/{media['id']}")
    assert response.json()["projects_affected"] == 1

    projects = client.get("/api/projects").json()["projects"]
    assert [item["id"] for item in projects] == [project["id"]]


def test_deleting_twice_is_not_found_rather_than_an_error(client):
    login(client)
    media = upload(client)
    client.delete(f"/api/media/{media['id']}")
    assert client.delete(f"/api/media/{media['id']}").status_code == 404


def test_someone_elses_file_cannot_be_deleted(client):
    login(client)
    media = upload(client)
    register_second_user(client, "friend")
    assert client.delete(f"/api/media/{media['id']}").status_code == 404


def test_signing_in_is_required(client):
    login(client)
    media = upload(client)
    client.post("/api/auth/logout")
    assert client.delete(f"/api/media/{media['id']}").status_code == 401


def test_an_unknown_id_is_not_found(client):
    login(client)
    assert client.delete("/api/media/no-such-thing").status_code == 404



def test_the_light_listing_leaves_transcripts_out(client):
    """1.5 MB a poll, per tab, for a field nothing in the poll read."""
    login(client)
    media = upload(client)
    from app.db.models import MediaAsset
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        row = db.get(MediaAsset, media["id"])
        row.transcript_json = '{"segments": [{"id": 1, "start": 0, "end": 1, "text": "hi"}]}'
        db.commit()

    light = client.get("/api/media?transcripts=0").json()["media"][0]
    assert "transcript" not in light and light["has_transcript"] is True
    full = client.get("/api/media").json()["media"][0]
    assert full["transcript"]["segments"][0]["text"] == "hi"
    one = client.get(f"/api/media/{media['id']}").json()["media"]
    assert one["transcript"]["segments"][0]["text"] == "hi"
