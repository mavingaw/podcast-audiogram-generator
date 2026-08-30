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

    from app.services import preview

    client.get(f"/api/projects/{project_id}/preview.m4a")
    assert len(list((settings.work_dir / "previews").glob("*.m4a"))) == 1
    # Serving touches the file (that is how the sweep knows it was opened),
    # so "not cut again" is checked by counting cuts, not by the timestamp.
    cuts = []
    original = preview._cut
    monkeypatch.setattr(preview, "_cut", lambda *a, **k: (cuts.append(1), original(*a, **k))[1])
    client.get(f"/api/projects/{project_id}/preview.m4a")
    assert cuts == [], "the clip was cut again"


@ffmpeg_required
def test_someone_elses_clip_is_not_found(monkeypatch, tmp_path):
    client, project_id = seeded(monkeypatch, tmp_path)
    register_second_user(client, "friend")
    assert client.get(f"/api/projects/{project_id}/preview.m4a").status_code == 404


@ffmpeg_required
def test_saving_the_range_cuts_the_preview_ahead_of_time(monkeypatch, tmp_path):
    """Open in Studio should find the file already there."""
    import time

    client, project_id = seeded(monkeypatch, tmp_path)
    from app.core.config import settings

    client.patch(f"/api/projects/{project_id}", json={"clip_start": 3.0, "clip_end": 9.0})
    expected = settings.work_dir / "previews" / "{}-3.000-9.000.m4a"
    for _ in range(50):
        if any(p.name.endswith("-3.000-9.000.m4a") for p in (settings.work_dir / "previews").glob("*.m4a")):
            break
        time.sleep(0.1)
    assert any(p.name.endswith("-3.000-9.000.m4a") for p in (settings.work_dir / "previews").glob("*.m4a")),         "the preview was not cut in the background"



def test_stale_previews_are_swept_and_opened_ones_are_kept(monkeypatch, tmp_path):
    """A clip edge nudged ten times leaves ten files; nothing else deletes them."""
    import os
    import time

    from tests.test_api import create_test_client

    create_test_client(monkeypatch, tmp_path)
    from app.core.config import settings
    from app.services import preview

    cache = settings.work_dir / "previews"
    cache.mkdir(parents=True, exist_ok=True)
    old = cache / "m-1.000-2.000.m4a"
    fresh = cache / "m-3.000-4.000.m4a"
    old.write_bytes(b"x")
    fresh.write_bytes(b"x")
    long_ago = time.time() - 30 * 86400
    os.utime(old, (long_ago, long_ago))

    assert preview.sweep() == 1
    assert not old.exists() and fresh.exists()
