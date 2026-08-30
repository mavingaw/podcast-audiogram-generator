"""Intro and outro videos on every export."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services import branding
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


def test_only_short_videos_qualify(client):
    login(client)
    assert client.get("/api/settings/branding").json() == {"intro": None, "outro": None}
    audio = upload(client, "song.mp3", "audio/mpeg")
    r = client.put("/api/settings/branding", json={"role": "intro", "media_id": audio["id"]})
    assert r.status_code == 400 and "video" in r.json()["detail"].lower()
    clip = upload(client, "intro.mp4", "video/mp4")
    r = client.put("/api/settings/branding", json={"role": "intro", "media_id": clip["id"]})
    assert r.status_code == 200 and r.json()["intro"] == clip["id"]
    # Clearing.
    r = client.put("/api/settings/branding", json={"role": "intro", "media_id": None})
    assert r.json()["intro"] is None
    assert client.put("/api/settings/branding", json={"role": "sideways", "media_id": None}).status_code == 400


def test_a_long_video_is_refused(client):
    from app.db.models import MediaAsset
    from app.db.session import SessionLocal

    login(client)
    clip = upload(client, "movie.mp4", "video/mp4")
    with SessionLocal() as db:
        db.get(MediaAsset, clip["id"]).duration_seconds = 120.0
        db.commit()
    r = client.put("/api/settings/branding", json={"role": "outro", "media_id": clip["id"]})
    assert r.status_code == 400 and "under 15" in r.json()["detail"]


def test_stitch_without_choices_is_a_no_op(client, tmp_path):
    from app.db.session import SessionLocal

    login(client)
    video = tmp_path / "main.mp4"
    video.write_bytes(b"\x00" * 64)
    with SessionLocal() as db:
        from app.db.models import User

        user = db.query(User).first()
        assert branding.stitch(db, user.id, video, 1080, 1920, tmp_path) is False
    assert video.read_bytes() == b"\x00" * 64


def test_stitch_joins_and_survives_ffmpeg_failure(client, tmp_path, monkeypatch):
    from app.db.session import SessionLocal
    from app.db.models import User
    from app.api.routes import settings as route_settings

    login(client)
    clip = upload(client, "intro.mp4", "video/mp4")
    client.put("/api/settings/branding", json={"role": "intro", "media_id": clip["id"]})

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        # The normalise step and the concat step both "produce" their file.
        out = Path(cmd[-1])
        out.write_bytes(b"JOINED")
        class R:
            returncode = 0
            stderr = ""
        return R()

    monkeypatch.setattr(branding.subprocess, "run", fake_run)
    video = tmp_path / "main.mp4"
    video.write_bytes(b"MAIN")
    with SessionLocal() as db:
        user = db.query(User).first()
        assert branding.stitch(db, user.id, video, 1080, 1920, tmp_path) is True
    assert video.read_bytes() == b"JOINED"
    assert any("-f" in c and "concat" in c for c in calls)

    # And when ffmpeg breaks, the render is untouched.
    def broken_run(cmd, **kwargs):
        class R:
            returncode = 1
            stderr = "boom"
        return R()

    monkeypatch.setattr(branding.subprocess, "run", broken_run)
    video.write_bytes(b"MAIN")
    with SessionLocal() as db:
        user = db.query(User).first()
        # The cached segment from the pass above still exists, so stitch
        # proceeds to the concat, which fails both ways -> False, untouched.
        assert branding.stitch(db, user.id, video, 1080, 1920, tmp_path) is False
    assert video.read_bytes() == b"MAIN"
