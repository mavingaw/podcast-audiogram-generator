"""The approval inbox.

A feed cuts clips while nobody is looking. Without somewhere for them to be
looked at, they land in the library and you have to go and find them — which is
the difference between automation you trust and automation you stop using.

The machine proposes; a person disposes.
"""

from __future__ import annotations

import json

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
    "Nobody warns you that the light goes at three in the afternoon."
)


def transcript() -> dict:
    words, cursor = [], 0.0
    for token in LONG.split():
        words.append({"text": token, "start": round(cursor, 3), "end": round(cursor + 0.28, 3)})
        cursor += 0.36
    return {
        "language": "en", "duration": words[-1]["end"] + 1,
        "segments": [{"id": 1, "speaker": "s", "start": 0.0, "end": words[-1]["end"],
                      "text": LONG, "words": words}],
    }


def seeded(monkeypatch, tmp_path):
    import uuid

    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})

    # After the reload, never before: see create_test_client's docstring.
    from app.db.models import MediaAsset, User
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        owner = db.query(User).first()
        asset = MediaAsset(
            owner_id=owner.id, original_name="episode nine.mp3",
            stored_name=f"{uuid.uuid4()}.mp3", content_type="audio/mpeg",
            size_bytes=1, duration_seconds=200.0,
            transcript_json=json.dumps(transcript()),
        )
        db.add(asset)
        db.commit()
        return client, asset.id


def make_pending(client, media_id, count=3):
    """Clips as a feed would make them: unrendered and unseen."""
    from app.db.models import MediaAsset
    from app.db.session import SessionLocal
    from app.services.batching import make_clips

    with SessionLocal() as db:
        media = db.get(MediaAsset, media_id)
        created = make_clips(
            db, owner_id=media.owner_id, media=media, count=count,
            render=False, source="feed", review_state="pending",
        )
        return [item.id for item in created]


# --------------------------------------------------------------------------
# What lands in the inbox
# --------------------------------------------------------------------------


def test_feed_clips_wait_to_be_looked_at(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    made = make_pending(client, media_id)
    body = client.get("/api/inbox").json()
    assert body["count"] == len(made)
    assert {clip["id"] for clip in body["clips"]} == set(made)


def test_clips_somebody_made_do_not_wait(monkeypatch, tmp_path):
    """A clip you made by hand was already a decision."""
    client, media_id = seeded(monkeypatch, tmp_path)
    client.post(f"/api/media/{media_id}/batch", json={"count": 2, "render": False})
    assert client.get("/api/inbox").json()["count"] == 0


def test_a_new_project_is_approved_by_default(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    project = client.post("/api/projects", json={"title": "Mine"}).json()["project"]
    assert project["review_state"] == "approved"
    assert project["source"] == "manual"


def test_the_inbox_says_which_episode_a_clip_came_from(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    make_pending(client, media_id, count=1)
    clip = client.get("/api/inbox").json()["clips"][0]
    assert clip["episode"] == "episode nine.mp3"
    assert clip["rendered"] is False


# --------------------------------------------------------------------------
# Approving
# --------------------------------------------------------------------------


def test_approving_clears_it_from_the_inbox(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    made = make_pending(client, media_id)
    client.post(f"/api/projects/{made[0]}/approve")
    remaining = client.get("/api/inbox").json()
    assert made[0] not in {clip["id"] for clip in remaining["clips"]}
    assert remaining["count"] == len(made) - 1


def test_approving_queues_the_render(monkeypatch, tmp_path):
    """A feed clip is prepared but not exported, so approval is what pays for it."""
    client, media_id = seeded(monkeypatch, tmp_path)
    made = make_pending(client, media_id, count=1)
    body = client.post(f"/api/projects/{made[0]}/approve").json()
    assert body["job"] is not None
    assert body["job"]["kind"] == "render"
    assert body["project"]["review_state"] == "approved"


def test_approving_an_already_rendered_clip_does_not_render_it_again(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    from app.api.routes import settings

    made = make_pending(client, media_id, count=1)
    folder = settings.outputs_dir / made[0]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "audiogram.mp4").write_bytes(b"already done")

    assert client.post(f"/api/projects/{made[0]}/approve").json()["job"] is None


def test_approving_twice_does_not_queue_two_renders(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    made = make_pending(client, media_id, count=1)
    client.post(f"/api/projects/{made[0]}/approve")
    assert client.post(f"/api/projects/{made[0]}/approve").json()["job"] is None


# --------------------------------------------------------------------------
# Rejecting
# --------------------------------------------------------------------------


def test_rejecting_removes_the_clip(monkeypatch, tmp_path):
    """An inbox that fills with things you said no to is one you stop opening."""
    client, media_id = seeded(monkeypatch, tmp_path)
    made = make_pending(client, media_id)
    assert client.post(f"/api/projects/{made[0]}/reject").status_code == 200
    assert client.get("/api/inbox").json()["count"] == len(made) - 1
    assert made[0] not in {p["id"] for p in client.get("/api/projects").json()["projects"]}


def test_rejecting_removes_its_output(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    from app.api.routes import settings

    made = make_pending(client, media_id, count=1)
    folder = settings.outputs_dir / made[0]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "audiogram.mp4").write_bytes(b"x")

    client.post(f"/api/projects/{made[0]}/reject")
    assert not folder.exists()


def test_rejecting_cancels_work_in_flight(monkeypatch, tmp_path):
    """Rendering a clip somebody just threw away is wasted GPU time."""
    client, media_id = seeded(monkeypatch, tmp_path)
    made = make_pending(client, media_id, count=1)
    client.post(f"/api/projects/{made[0]}/approve")

    from app.db.models import Job
    from app.db.session import SessionLocal

    client.post(f"/api/projects/{made[0]}/reject")
    with SessionLocal() as db:
        assert db.query(Job).filter(Job.subject_id == made[0]).count() == 0


def test_a_rejected_moment_can_be_suggested_again(monkeypatch, tmp_path):
    """Rejected is gone, not remembered — the same moment may cut better later."""
    client, media_id = seeded(monkeypatch, tmp_path)
    made = make_pending(client, media_id, count=2)
    for clip in made:
        client.post(f"/api/projects/{clip}/reject")

    again = client.post(
        f"/api/media/{media_id}/batch", json={"count": 2, "render": False}
    ).json()
    assert len(again["projects"]) > 0


# --------------------------------------------------------------------------
# Ownership
# --------------------------------------------------------------------------


def test_the_inbox_is_private(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    made = make_pending(client, media_id, count=1)

    client.post("/api/auth/logout")
    register_second_user(client, "guest", "another-password")
    assert client.get("/api/inbox").json()["count"] == 0
    assert client.post(f"/api/projects/{made[0]}/approve").status_code == 404
    assert client.post(f"/api/projects/{made[0]}/reject").status_code == 404
