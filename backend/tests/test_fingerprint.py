"""Recognising a render that has already been done.

The fingerprint is only useful if it is stable across the round trips a project
actually makes — through JSON, through SQLite, through a float that came back a
bit lighter than it went in. These pin that, and pin what it must still tell
apart.
"""

from __future__ import annotations

import json

from app.services.fingerprint import (
    SIGNIFICANT,
    canonical_form,
    fingerprint,
    is_duplicate,
    matches,
)


def project(**overrides) -> dict:
    base = {
        "id": "project-1",
        "title": "Episode 12",
        "media_id": "media-1",
        "clip_start": 5.0,
        "clip_end": 41.5,
        "aspect_ratio": "9:16",
        "scene": {
            "captionPreset": "social",
            "accent": "#89CFF0",
            "layers": [
                {"id": "waveform", "type": "waveform", "x": 12, "y": 71,
                 "width": 76, "height": 9},
                {"id": "captions", "type": "captions", "x": 12, "y": 59},
            ],
        },
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# Stability
# --------------------------------------------------------------------------


def test_the_same_project_fingerprints_the_same_twice():
    assert fingerprint(project()) == fingerprint(project())


def test_key_order_does_not_change_the_fingerprint():
    """A scene round-tripped through JSON comes back in whatever order it likes."""
    forward = project()
    reversed_keys = {key: forward[key] for key in reversed(list(forward))}
    reversed_keys["scene"] = {
        key: forward["scene"][key] for key in reversed(list(forward["scene"]))
    }
    assert fingerprint(forward) == fingerprint(reversed_keys)


def test_a_json_round_trip_does_not_change_the_fingerprint():
    original = project()
    restored = json.loads(json.dumps(original))
    assert fingerprint(original) == fingerprint(restored)


def test_numbers_compare_by_value_not_by_spelling():
    """0, 0.0 and 0.000 are the same clip start."""
    assert fingerprint(project(clip_start=0)) == fingerprint(project(clip_start=0.0))
    assert fingerprint(project(clip_start=5)) == fingerprint(project(clip_start=5.000))


def test_a_float_that_drifted_a_bit_still_matches():
    """Storage and arithmetic each round-trip floats their own way."""
    assert fingerprint(project(clip_start=5.0)) == fingerprint(
        project(clip_start=5.0000001)
    )


def test_negative_zero_matches_zero():
    assert fingerprint(project(clip_start=-0.0)) == fingerprint(project(clip_start=0.0))


def test_nested_scene_numbers_are_normalised_too():
    one = project()
    other = project()
    other["scene"]["layers"][0]["y"] = 71.0000001
    assert fingerprint(one) == fingerprint(other)


def test_the_fingerprint_is_a_hex_digest():
    value = fingerprint(project())
    assert len(value) == 64
    assert all(character in "0123456789abcdef" for character in value)


# --------------------------------------------------------------------------
# What it must still tell apart
# --------------------------------------------------------------------------


def test_a_different_clip_range_is_a_different_render():
    assert fingerprint(project()) != fingerprint(project(clip_end=42.0))
    assert fingerprint(project()) != fingerprint(project(clip_start=6.0))


def test_a_different_shape_is_a_different_render():
    assert fingerprint(project()) != fingerprint(project(aspect_ratio="16:9"))


def test_a_different_source_is_a_different_render():
    assert fingerprint(project()) != fingerprint(project(media_id="media-2"))


def test_a_changed_scene_is_a_different_render():
    changed = project()
    changed["scene"]["captionPreset"] = "kinder"
    assert fingerprint(project()) != fingerprint(changed)


def test_moving_a_layer_is_a_different_render():
    moved = project()
    moved["scene"]["layers"][0]["y"] = 40
    assert fingerprint(project()) != fingerprint(moved)


def test_restacking_layers_is_a_different_render():
    """Layer order is the stacking order, so it is not sorted away."""
    restacked = project()
    restacked["scene"]["layers"].reverse()
    assert fingerprint(project()) != fingerprint(restacked)


def test_true_does_not_fingerprint_as_one():
    """bool is an int subclass, and normalising numbers must not flatten it."""
    yes = project()
    yes["scene"]["visible"] = True
    one = project()
    one["scene"]["visible"] = 1
    assert fingerprint(yes) != fingerprint(one)


def test_a_difference_below_a_millisecond_is_not_a_different_render():
    """Clip times reach FFmpeg as `%.3f`, so nothing finer can change the file."""
    assert fingerprint(project(clip_start=5.0)) == fingerprint(project(clip_start=5.0001))


# --------------------------------------------------------------------------
# What it deliberately ignores
# --------------------------------------------------------------------------


def test_the_title_does_not_change_the_fingerprint():
    assert fingerprint(project()) == fingerprint(project(title="Something else"))


def test_ownership_and_timestamps_do_not_change_the_fingerprint():
    other = project(id="project-2", owner_id="somebody", updated_at="2026-01-01")
    assert fingerprint(project()) == fingerprint(other)


def test_only_the_significant_fields_are_read():
    assert set(canonical_form(project())) == set(SIGNIFICANT)


# --------------------------------------------------------------------------
# Edge cases
# --------------------------------------------------------------------------


def test_an_empty_project_still_fingerprints():
    assert len(fingerprint({})) == 64


def test_a_non_dict_is_tolerated():
    assert fingerprint(None) == fingerprint({})
    assert fingerprint([]) == fingerprint({})


def test_a_missing_scene_and_an_empty_one_differ():
    """One has never been designed; the other has been emptied."""
    assert fingerprint(project(scene=None)) != fingerprint(project(scene={}))


def test_unserialisable_values_do_not_raise():
    class Odd:
        pass

    awkward = project()
    awkward["scene"]["thing"] = Odd()
    assert len(fingerprint(awkward)) == 64


# --------------------------------------------------------------------------
# The duplicate check
# --------------------------------------------------------------------------


def test_a_seen_fingerprint_is_a_duplicate():
    value = fingerprint(project())
    assert is_duplicate({value}, value) is True
    assert is_duplicate([value, "other"], value) is True


def test_an_unseen_fingerprint_is_not():
    assert is_duplicate({"something"}, fingerprint(project())) is False


def test_nothing_seen_yet_is_not_a_duplicate():
    assert is_duplicate(set(), fingerprint(project())) is False
    assert is_duplicate(None, fingerprint(project())) is False


def test_an_empty_candidate_is_never_a_duplicate():
    """Better to render twice than to skip on a missing fingerprint."""
    assert is_duplicate({""}, "") is False


def test_matches_compares_two_projects():
    assert matches(project(), project()) is True
    assert matches(project(), project(aspect_ratio="1:1")) is False


# --------------------------------------------------------------------------
# Not rendering the same thing twice
# --------------------------------------------------------------------------
#
# The module was written and tested but never wired in, which made it a
# well-covered no-op. These cover the behaviour people actually get.


from tests.test_api import create_test_client


def project_with_media(monkeypatch, tmp_path):
    import json as jsonlib
    import uuid

    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})

    from app.db.models import MediaAsset, User
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        owner = db.query(User).first()
        asset = MediaAsset(
            owner_id=owner.id, original_name="ep.mp3",
            stored_name=f"{uuid.uuid4()}.mp3", content_type="audio/mpeg",
            size_bytes=1, duration_seconds=120.0,
        )
        db.add(asset)
        db.commit()
        media_id = asset.id

    project = client.post(
        "/api/projects", json={"title": "Clip", "media_id": media_id}
    ).json()["project"]
    return client, project


def rendered(client, project_id):
    """Pretend the render finished and left a file."""
    from app.api.routes import settings
    from app.db.models import Job, JobStatus
    from app.db.session import SessionLocal

    folder = settings.outputs_dir / project_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "audiogram.mp4").write_bytes(b"a rendered clip")
    with SessionLocal() as db:
        job = db.query(Job).filter(Job.subject_id == project_id).order_by(
            Job.created_at.desc()
        ).first()
        job.status = JobStatus.complete
        db.commit()


def test_a_second_export_of_an_unchanged_clip_is_not_rendered_again(monkeypatch, tmp_path):
    client, project = project_with_media(monkeypatch, tmp_path)
    first = client.post(f"/api/projects/{project['id']}/render").json()
    assert first["reused"] is False
    rendered(client, project["id"])

    second = client.post(f"/api/projects/{project['id']}/render").json()
    assert second["reused"] is True
    assert second["job"]["id"] == first["job"]["id"]


def test_changing_the_clip_renders_again(monkeypatch, tmp_path):
    client, project = project_with_media(monkeypatch, tmp_path)
    client.post(f"/api/projects/{project['id']}/render")
    rendered(client, project["id"])

    client.patch(f"/api/projects/{project['id']}", json={"clip_end": 30.0})
    assert client.post(f"/api/projects/{project['id']}/render").json()["reused"] is False


def test_changing_the_design_renders_again(monkeypatch, tmp_path):
    """The scene decides the pixels, so a design change is a different render."""
    client, project = project_with_media(monkeypatch, tmp_path)
    client.post(f"/api/projects/{project['id']}/render")
    rendered(client, project["id"])

    client.patch(f"/api/projects/{project['id']}", json={"scene": {"captionPreset": "shout"}})
    assert client.post(f"/api/projects/{project['id']}/render").json()["reused"] is False


def test_renaming_a_project_does_not_force_a_re_render(monkeypatch, tmp_path):
    """A title changes nothing about the video."""
    client, project = project_with_media(monkeypatch, tmp_path)
    client.post(f"/api/projects/{project['id']}/render")
    rendered(client, project["id"])

    client.patch(f"/api/projects/{project['id']}", json={"title": "A better name"})
    assert client.post(f"/api/projects/{project['id']}/render").json()["reused"] is True


def test_a_missing_output_is_rendered_again(monkeypatch, tmp_path):
    """Matching fingerprints are no use if somebody deleted the file."""
    import shutil

    client, project = project_with_media(monkeypatch, tmp_path)
    client.post(f"/api/projects/{project['id']}/render")
    rendered(client, project["id"])

    # After the client, never before: see create_test_client's docstring.
    from app.api.routes import settings

    shutil.rmtree(settings.outputs_dir / project["id"])

    assert client.post(f"/api/projects/{project['id']}/render").json()["reused"] is False


def test_a_double_click_does_not_queue_two_renders(monkeypatch, tmp_path):
    """The job is still queued, so there is nothing to compare a file against."""
    client, project = project_with_media(monkeypatch, tmp_path)
    first = client.post(f"/api/projects/{project['id']}/render").json()
    second = client.post(f"/api/projects/{project['id']}/render").json()
    assert second["reused"] is True
    assert second["job"]["id"] == first["job"]["id"]
    assert "already rendering" in second["reason"]


def test_force_renders_anyway(monkeypatch, tmp_path):
    client, project = project_with_media(monkeypatch, tmp_path)
    client.post(f"/api/projects/{project['id']}/render")
    rendered(client, project["id"])
    assert client.post(
        f"/api/projects/{project['id']}/render?force=true"
    ).json()["reused"] is False
