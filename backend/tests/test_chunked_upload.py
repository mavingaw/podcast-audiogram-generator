"""Uploading a file in pieces.

The single-request path was fine on the LAN and broken from anywhere else:
Cloudflare's free plan refuses a body over 100 MB at its own edge, so an
ordinary podcast episode produced a 413 from a machine we do not run, with no
log line on the server and nothing in the UI. These tests cover the path that
goes under that limit, and the ways a partial upload can go wrong — which is
most of the risk, since a mistake here corrupts a file rather than failing.
"""

from __future__ import annotations

import time

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


def begin(client, name="episode.mp3", size=1024, kind="audio/mpeg"):
    return client.post("/api/media/upload/begin", json={
        "filename": name, "content_type": kind, "total_bytes": size,
    })


def send(client, upload_id, index, data):
    return client.put(
        f"/api/media/upload/{upload_id}/chunk/{index}", content=data,
        headers={"content-type": "application/octet-stream"},
    )


# --------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------


def test_a_file_sent_in_pieces_arrives_whole(client, tmp_path):
    login(client)
    payload = bytes(range(256)) * 40  # 10240 bytes, and order-sensitive
    started = begin(client, size=len(payload))
    assert started.status_code == 200
    upload_id = started.json()["upload_id"]

    for index in range(0, 10):
        chunk = payload[index * 1024:(index + 1) * 1024]
        assert send(client, upload_id, index, chunk).status_code == 200

    finished = client.post(f"/api/media/upload/{upload_id}/finish")
    assert finished.status_code == 200
    media = finished.json()["media"]
    assert media["size_bytes"] == len(payload)

    from app.core.config import settings
    from app.db.models import MediaAsset
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        stored = db.get(MediaAsset, media["id"]).stored_name
    written = (settings.uploads_dir / stored).read_bytes()
    assert written == payload, "the reassembled file is not the file that was sent"


def test_the_chunked_path_queues_the_same_work_as_a_normal_upload(client):
    """A file that arrived in pieces must be indistinguishable afterwards."""
    login(client)
    upload_id = begin(client, size=4).json()["upload_id"]
    send(client, upload_id, 0, b"data")
    kinds = {
        job["kind"]
        for job in client.post(f"/api/media/upload/{upload_id}/finish").json()["jobs"]
    }
    assert kinds == {"analyze_media", "waveform", "transcribe"}


def test_progress_is_reported_as_it_goes(client):
    login(client)
    upload_id = begin(client, size=300).json()["upload_id"]
    body = send(client, upload_id, 0, b"x" * 100).json()
    assert body["received"] == 100
    assert body["total_bytes"] == 300
    assert body["next_index"] == 1


# --------------------------------------------------------------------------
# Refusing early
# --------------------------------------------------------------------------


def test_an_unsupported_file_is_refused_before_it_is_sent(client):
    """Twenty minutes of uploading should not end in 'not supported'."""
    login(client)
    response = begin(client, name="voice.aiff", size=1024)
    assert response.status_code == 415


def test_an_empty_file_is_refused(client):
    login(client)
    assert begin(client, size=0).status_code == 400


def test_a_file_over_the_server_limit_is_refused(client):
    login(client)
    from app.core.config import settings

    response = begin(client, size=settings.max_upload_bytes + 1)
    assert response.status_code == 413


def test_signing_in_is_required(client):
    login(client)
    client.post("/api/auth/logout")
    assert begin(client).status_code == 401


# --------------------------------------------------------------------------
# Going wrong midway
# --------------------------------------------------------------------------


def test_a_chunk_out_of_order_is_refused(client):
    """Accepting it would silently write the file with a hole in it."""
    login(client)
    upload_id = begin(client, size=300).json()["upload_id"]
    send(client, upload_id, 0, b"x" * 100)
    assert send(client, upload_id, 2, b"x" * 100).status_code == 409


def test_resending_the_last_chunk_is_not_an_error(client):
    """A client that lost the response should not have to start over."""
    login(client)
    upload_id = begin(client, size=200).json()["upload_id"]
    send(client, upload_id, 0, b"x" * 100)
    assert send(client, upload_id, 0, b"x" * 100).status_code == 200
    send(client, upload_id, 1, b"y" * 100)
    assert client.post(f"/api/media/upload/{upload_id}/finish").json()[
        "media"]["size_bytes"] == 200


def test_more_bytes_than_declared_is_refused(client):
    login(client)
    upload_id = begin(client, size=100).json()["upload_id"]
    assert send(client, upload_id, 0, b"x" * 500).status_code == 413


def test_finishing_early_is_refused(client):
    """Otherwise a truncated file enters the library and fails at transcription."""
    login(client)
    upload_id = begin(client, size=1000).json()["upload_id"]
    send(client, upload_id, 0, b"x" * 100)
    response = client.post(f"/api/media/upload/{upload_id}/finish")
    assert response.status_code == 400
    assert "missing" in response.json()["detail"]


def test_an_unknown_upload_is_not_found(client):
    login(client)
    assert send(client, "no-such-upload", 0, b"x").status_code == 404


def test_aborting_removes_the_partial_file(client):
    login(client)
    from app.services import chunked_upload

    upload_id = begin(client, size=1000).json()["upload_id"]
    send(client, upload_id, 0, b"x" * 100)
    assert list(chunked_upload.staging_dir().glob("*.part"))
    client.delete(f"/api/media/upload/{upload_id}")
    assert not list(chunked_upload.staging_dir().glob("*.part"))


# --------------------------------------------------------------------------
# Someone else's upload
# --------------------------------------------------------------------------


def test_another_account_cannot_add_to_an_upload(client):
    """A guessed id must not let one friend write into another's file."""
    login(client)
    upload_id = begin(client, size=200).json()["upload_id"]

    from tests.test_api import register_second_user

    register_second_user(client, "friend")
    # Reported as missing rather than forbidden, so ids cannot be probed.
    assert send(client, upload_id, 0, b"x" * 100).status_code == 404
    assert client.post(f"/api/media/upload/{upload_id}/finish").status_code == 404


# --------------------------------------------------------------------------
# Housekeeping
# --------------------------------------------------------------------------


def test_abandoned_uploads_are_swept(client):
    """A closed tab must not park half an episode on the disk forever."""
    login(client)
    from app.services import chunked_upload

    upload_id = begin(client, size=1000).json()["upload_id"]
    send(client, upload_id, 0, b"x" * 100)

    assert chunked_upload.sweep() == 0, "a live upload was swept"
    later = time.time() + chunked_upload.STALE_SECONDS + 60
    assert chunked_upload.sweep(now=later) == 1
    assert not list(chunked_upload.staging_dir().glob("*.part"))


def test_the_staging_directory_does_not_look_like_media(client):
    """The library lists the uploads directory; parts must not show up in it."""
    login(client)
    upload_id = begin(client, size=1000).json()["upload_id"]
    send(client, upload_id, 0, b"x" * 100)
    assert client.get("/api/media").json()["media"] == []
