"""Asking for a transcript again after a failed one.

A transcription that failed — the model could not be fetched, the box was
restarted mid-job — used to leave the source listed as "analyzing" for ever,
with re-uploading the file as the only way past. This is the way past.
"""

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


def upload(client, name="episode.mp3", content_type="audio/mpeg") -> dict:
    response = client.post(
        "/api/media/upload",
        files={"file": (name, b"bytes here", content_type)},
    )
    assert response.status_code == 200, response.text
    return response.json()["media"]


def test_a_source_without_a_transcript_can_be_queued_again(client):
    login(client)
    media = upload(client)
    r = client.post(f"/api/media/{media['id']}/transcribe")
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    assert job["kind"] == "transcribe" and job["status"] in ("queued", "running")


def test_asking_twice_does_not_queue_twice(client):
    login(client)
    media = upload(client)
    first = client.post(f"/api/media/{media['id']}/transcribe").json()["job"]
    second = client.post(f"/api/media/{media['id']}/transcribe").json()["job"]
    assert first["id"] == second["id"]


def test_an_image_is_refused(client):
    login(client)
    media = upload(client, name="art.png", content_type="image/png")
    assert client.post(f"/api/media/{media['id']}/transcribe").status_code == 400


def test_somebody_elses_file_is_not_found(client):
    from tests.test_api import register_second_user

    login(client)
    media = upload(client)
    register_second_user(client, "friend")
    assert client.post(f"/api/media/{media['id']}/transcribe").status_code == 404
