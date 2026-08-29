"""The Studio player gets the clip's own audio, not the episode.

Studio used to play the full episode file and seek into it: from outside the
LAN that meant 90 MB through the tunnel before the first second played, and a
scrubber over ninety minutes the clip did not contain.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from tests.test_api import create_test_client, register_second_user

ffmpeg_required = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def seconds_of(data: bytes, tmp_path) -> float:
    path = tmp_path / "probe.m4a"
    path.write_bytes(data)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def seeded(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "owner", "password": "Passw0rd!enough"})
    wav = tmp_path / "ep.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=30", "-ar", "16000", "-ac", "1", str(wav)],
        check=True, capture_output=True,
    )
    media = client.post("/api/media/upload", files={"file": ("ep.wav", wav.read_bytes(), "audio/wav")}).json()["media"]
    project = client.post("/api/projects", json={"title": "c", "media_id": media["id"]}).json()["project"]
    client.patch(f"/api/projects/{project['id']}", json={"clip_start": 10.0, "clip_end": 14.5})
    return client, project["id"]


@ffmpeg_required
def test_the_preview_is_exactly_the_clip(monkeypatch, tmp_path):
    client, project_id = seeded(monkeypatch, tmp_path)
    response = client.get(f"/api/projects/{project_id}/preview.m4a")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mp4")
    assert seconds_of(response.content, tmp_path) == pytest.approx(4.5, abs=0.15)
    # A clip, not an episode: a few tens of kilobytes rather than the file.
    assert len(response.content) < 200_000


@ffmpeg_required
def test_moving_the_clip_gives_a_different_file(monkeypatch, tmp_path):
    client, project_id = seeded(monkeypatch, tmp_path)
    first = client.get(f"/api/projects/{project_id}/preview.m4a").content
    client.patch(f"/api/projects/{project_id}", json={"clip_start": 2.0, "clip_end": 12.0})
    second = client.get(f"/api/projects/{project_id}/preview.m4a").content
    assert seconds_of(second, tmp_path) == pytest.approx(10.0, abs=0.15)
    assert first != second


@ffmpeg_required
def test_a_repeat_request_is_served_from_the_cache(monkeypatch, tmp_path):
    client, project_id = seeded(monkeypatch, tmp_path)
    from app.core.config import settings

    client.get(f"/api/projects/{project_id}/preview.m4a")
    cached = list((settings.work_dir / "previews").glob("*.m4a"))
    assert len(cached) == 1
    stamp = cached[0].stat().st_mtime_ns
    client.get(f"/api/projects/{project_id}/preview.m4a")
    assert cached[0].stat().st_mtime_ns == stamp, "the clip was cut again"


@ffmpeg_required
def test_someone_elses_clip_is_not_found(monkeypatch, tmp_path):
    client, project_id = seeded(monkeypatch, tmp_path)
    register_second_user(client, "friend")
    assert client.get(f"/api/projects/{project_id}/preview.m4a").status_code == 404
