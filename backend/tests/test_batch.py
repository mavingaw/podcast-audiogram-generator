"""One episode into a set of clips, in one action.

This is what everything upstream is for: the suggestions know where the good
moments are, snapping keeps the cuts off the middle of words, and the render
lanes run several at once. Doing it one clip at a time is the part of the job
that makes people stop bothering.
"""

from __future__ import annotations

import io
import json
import zipfile

from tests.test_api import create_test_client, register_second_user

LONG = (
    "The thing nobody tells you about moving abroad is how ordinary it feels. "
    "You expect fireworks and instead you get a Tuesday. "
    "I used to think the hard part would be the paperwork and the language. "
    "It turns out the hard part is that nothing feels like an occasion any more. "
    "Everyone asks whether I miss home and I never know how to answer that. "
    "The truth is I miss a version of it that stopped existing years ago. "
    "So what do you actually do with that feeling when it arrives. "
    "Honestly you just make dinner and get on with the evening. "
    "The first winter was the one that nearly sent me back to the airport. "
    "Nobody warns you that the light goes at three in the afternoon. "
    "What people mean by settling in is really just lowering your expectations. "
    "And once I stopped waiting to feel settled I mostly did."
)


def speech(text: str, rate: float = 2.8) -> list[dict]:
    words, cursor, step = [], 0.0, 1.0 / rate
    for token in text.split():
        words.append({"text": token, "start": round(cursor, 3),
                      "end": round(cursor + step * 0.8, 3)})
        cursor += step
    return words


def transcript(words: list[dict]) -> dict:
    return {
        "language": "en",
        "duration": words[-1]["end"] + 1,
        "segments": [{"id": 1, "speaker": "s", "start": words[0]["start"],
                      "end": words[-1]["end"],
                      "text": " ".join(w["text"] for w in words), "words": words}],
    }


def seeded(monkeypatch, tmp_path, words=None):
    import uuid

    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})

    # After the reload, never before: see create_test_client's docstring.
    from app.db.models import MediaAsset, User
    from app.db.session import SessionLocal

    data = transcript(speech(LONG) if words is None else words) if words is not False else None
    with SessionLocal() as db:
        owner = db.query(User).first()
        asset = MediaAsset(
            owner_id=owner.id, original_name="episode one.mp3",
            stored_name=f"{uuid.uuid4()}.mp3", content_type="audio/mpeg",
            size_bytes=1, duration_seconds=200.0,
            transcript_json=json.dumps(data) if data else None,
        )
        db.add(asset)
        db.commit()
        return client, asset.id


# --------------------------------------------------------------------------
# Making a batch
# --------------------------------------------------------------------------


def test_a_batch_creates_several_clips(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    body = client.post(
        f"/api/media/{media_id}/batch", json={"count": 3, "render": False}
    ).json()
    assert 1 <= len(body["projects"]) <= 3
    assert all(p["media_id"] == media_id for p in body["projects"])


def test_each_clip_gets_its_own_moment(monkeypatch, tmp_path):
    """A batch of near-identical clips is not a batch."""
    client, media_id = seeded(monkeypatch, tmp_path)
    projects = client.post(
        f"/api/media/{media_id}/batch", json={"count": 4, "render": False}
    ).json()["projects"]
    for index, first in enumerate(projects):
        for second in projects[index + 1:]:
            overlap = (min(first["clip_end"], second["clip_end"])
                       - max(first["clip_start"], second["clip_start"]))
            shorter = min(first["clip_end"] - first["clip_start"],
                          second["clip_end"] - second["clip_start"])
            assert overlap <= 0.6 * shorter


def test_titles_come_from_the_audio(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    body = client.post(
        f"/api/media/{media_id}/batch", json={"count": 3, "render": False}
    ).json()
    for project in body["projects"]:
        stem = project["title"].rstrip("…").rstrip(".")
        assert stem in LONG, project["title"]


def test_running_it_twice_adds_rather_than_duplicates(monkeypatch, tmp_path):
    """Someone will press it again; that must not double the library."""
    client, media_id = seeded(monkeypatch, tmp_path)
    client.post(f"/api/media/{media_id}/batch", json={"count": 4, "render": False})
    second = client.post(
        f"/api/media/{media_id}/batch", json={"count": 4, "render": False}
    ).json()
    assert second["skipped"] > 0


def test_a_batch_queues_a_render_for_each_clip(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    body = client.post(f"/api/media/{media_id}/batch", json={"count": 3}).json()
    assert len(body["jobs"]) == len(body["projects"])
    assert all(job["kind"] == "render" for job in body["jobs"])


def test_a_batch_can_skip_rendering(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    body = client.post(
        f"/api/media/{media_id}/batch", json={"count": 2, "render": False}
    ).json()
    assert body["jobs"] == []


def test_a_batch_can_be_made_in_another_shape(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    body = client.post(
        f"/api/media/{media_id}/batch",
        json={"count": 2, "render": False, "aspect_ratio": "16:9"},
    ).json()
    assert all(p["aspect_ratio"] == "16:9" for p in body["projects"])


def test_a_saved_template_can_be_applied_to_the_whole_batch(monkeypatch, tmp_path):
    """A batch should come out on-brand without editing ten projects."""
    client, media_id = seeded(monkeypatch, tmp_path)
    source = client.post("/api/projects", json={"title": "Look"}).json()["project"]
    client.patch(
        f"/api/projects/{source['id']}",
        json={"scene": {"captionPreset": "shout", "waveStyle": "envelopeFine"}},
    )
    template = client.post(
        "/api/templates", json={"name": "Show look", "project_id": source["id"]}
    ).json()["template"]

    body = client.post(
        f"/api/media/{media_id}/batch",
        json={"count": 2, "render": False, "template_id": template["id"]},
    ).json()
    assert all(p["scene"]["captionPreset"] == "shout" for p in body["projects"])


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_media_without_a_transcript_is_refused(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path, words=False)
    response = client.post(f"/api/media/{media_id}/batch", json={"count": 3})
    assert response.status_code == 400
    assert "Transcribe" in response.json()["detail"]


def test_audio_with_nothing_clippable_says_so(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path, words=speech("Hello there."))
    response = client.post(f"/api/media/{media_id}/batch", json={"count": 3})
    assert response.status_code == 400
    assert "self-contained" in response.json()["detail"]


def test_the_count_is_bounded(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    assert client.post(f"/api/media/{media_id}/batch", json={"count": 0}).status_code == 422
    assert client.post(f"/api/media/{media_id}/batch", json={"count": 99}).status_code == 422


def test_an_unknown_shape_is_refused(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    assert client.post(
        f"/api/media/{media_id}/batch", json={"count": 2, "aspect_ratio": "3:7"}
    ).status_code == 400


def test_someone_elses_media_is_not_found(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    client.post("/api/auth/logout")
    register_second_user(client, "guest", "another-password")
    assert client.post(f"/api/media/{media_id}/batch", json={"count": 2}).status_code == 404


# --------------------------------------------------------------------------
# Taking the batch away
# --------------------------------------------------------------------------


def test_finished_clips_download_as_one_zip(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    from app.api.routes import settings

    projects = client.post(
        f"/api/media/{media_id}/batch", json={"count": 3, "render": False}
    ).json()["projects"]
    for project in projects:
        folder = settings.outputs_dir / project["id"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "audiogram.mp4").write_bytes(b"pretend video " + project["id"].encode())

    response = client.get(f"/api/media/{media_id}/exports.zip")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert len(archive.namelist()) == len(projects)
    assert all(name.endswith(".mp4") for name in archive.namelist())


def test_the_zip_is_named_after_the_episode(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    from app.api.routes import settings

    project = client.post(
        f"/api/media/{media_id}/batch", json={"count": 1, "render": False}
    ).json()["projects"][0]
    folder = settings.outputs_dir / project["id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "audiogram.mp4").write_bytes(b"x")

    response = client.get(f"/api/media/{media_id}/exports.zip")
    assert "episode one" in response.headers["content-disposition"]


def test_clips_that_share_a_title_do_not_overwrite_each_other(monkeypatch, tmp_path):
    """Two suggestions can start with the same sentence."""
    client, media_id = seeded(monkeypatch, tmp_path)
    from app.api.routes import settings

    # Made through the batch so they belong to this media, then given the same
    # title on purpose.
    projects = client.post(
        f"/api/media/{media_id}/batch", json={"count": 2, "render": False}
    ).json()["projects"]
    assert len(projects) == 2
    for project in projects:
        client.patch(f"/api/projects/{project['id']}", json={"title": "Same name"})
        folder = settings.outputs_dir / project["id"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "audiogram.mp4").write_bytes(b"x")

    response = client.get(f"/api/media/{media_id}/exports.zip")
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert len(names) == len(set(names)) == 2


def test_a_zip_with_nothing_rendered_is_not_found(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    client.post(f"/api/media/{media_id}/batch", json={"count": 2, "render": False})
    assert client.get(f"/api/media/{media_id}/exports.zip").status_code == 404


def test_someone_elses_zip_is_not_found(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    client.post("/api/auth/logout")
    register_second_user(client, "guest", "another-password")
    assert client.get(f"/api/media/{media_id}/exports.zip").status_code == 404
