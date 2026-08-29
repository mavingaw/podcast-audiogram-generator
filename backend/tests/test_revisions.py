"""Going back to how a clip was before.

Applying a template rewrites every layer, cutting words rewrites the audio, a
batch rewrites the lot. Each is worth having and none is comfortable without a
way back.

The interesting property is not that history is recorded — it is that it stays
*readable*. Snapshotting every PATCH would produce hundreds of entries, because
dragging a colour slider is a PATCH per frame, and a history nobody can read is
the same as no history at all.
"""

from __future__ import annotations

import time

import pytest

from tests.test_api import create_test_client, register_second_user


@pytest.fixture()
def client(monkeypatch, tmp_path):
    with create_test_client(monkeypatch, tmp_path) as test_client:
        yield test_client


def signed_in(client):
    client.post("/api/bootstrap", json={
        "username": "owner", "password": "Passw0rd!enough", "display_name": "Owner",
    })


def make_project(client, title="A clip") -> str:
    return client.post("/api/projects", json={"title": title}).json()["project"]["id"]


def revisions_of(client, project_id) -> list[dict]:
    return client.get(f"/api/projects/{project_id}/revisions").json()["revisions"]


def no_throttle(monkeypatch):
    """Coalescing is the point, but most tests are about everything else."""
    from app.services import revisions

    monkeypatch.setattr(revisions, "COALESCE_SECONDS", 0.0)


# --------------------------------------------------------------------------
# Recording
# --------------------------------------------------------------------------


def test_a_new_project_has_no_history(client):
    signed_in(client)
    assert revisions_of(client, make_project(client)) == []


def test_a_change_records_how_it_was_before(client, monkeypatch):
    signed_in(client)
    no_throttle(monkeypatch)
    project = make_project(client, "Original")
    client.patch(f"/api/projects/{project}", json={"title": "Renamed"})

    history = revisions_of(client, project)
    assert len(history) == 1
    assert history[0]["label"] == "Renamed"


def test_the_recorded_state_is_the_one_before_the_change(client, monkeypatch):
    """A snapshot of the new state would be useless for going back."""
    signed_in(client)
    no_throttle(monkeypatch)
    project = make_project(client, "Original")
    client.patch(f"/api/projects/{project}", json={"title": "Renamed"})

    revision = revisions_of(client, project)[0]["id"]
    restored = client.post(
        f"/api/projects/{project}/revisions/{revision}/restore"
    ).json()["project"]
    assert restored["title"] == "Original"


def test_history_is_newest_first(client, monkeypatch):
    signed_in(client)
    no_throttle(monkeypatch)
    project = make_project(client, "One")
    for title in ("Two", "Three", "Four"):
        client.patch(f"/api/projects/{project}", json={"title": title})
        time.sleep(0.01)

    history = revisions_of(client, project)
    assert len(history) == 3
    assert [item["created_at"] for item in history] == sorted(
        [item["created_at"] for item in history], reverse=True
    )


def test_setting_a_field_to_what_it_already_was_is_not_a_revision(client, monkeypatch):
    """A focus-then-blur save should not fill the history with nothing."""
    signed_in(client)
    no_throttle(monkeypatch)
    project = make_project(client, "Same")
    client.patch(f"/api/projects/{project}", json={"title": "Same"})
    client.patch(f"/api/projects/{project}", json={"title": "Same"})
    assert len(revisions_of(client, project)) <= 1


def test_a_review_state_change_is_not_worth_recording(client, monkeypatch):
    signed_in(client)
    no_throttle(monkeypatch)
    project = make_project(client)
    client.patch(f"/api/projects/{project}", json={"review_state": "approved"})
    assert revisions_of(client, project) == []


# --------------------------------------------------------------------------
# Staying readable
# --------------------------------------------------------------------------


def test_a_burst_of_edits_becomes_one_entry(client):
    """Dragging a slider is a PATCH per frame; the history must survive it."""
    signed_in(client)
    project = make_project(client)
    for value in range(20):
        client.patch(
            f"/api/projects/{project}",
            json={"scene": {"accent": f"#0000{value:02x}"}},
        )
    assert len(revisions_of(client, project)) == 1


def test_a_template_is_recorded_even_inside_the_quiet_window(client):
    """It rewrites every layer at once, which is when a way back matters most."""
    signed_in(client)
    project = make_project(client)
    client.patch(f"/api/projects/{project}", json={"scene": {"accent": "#111111"}})
    client.patch(
        f"/api/projects/{project}",
        json={"scene": {"accent": "#111111", "template": "bold"}},
    )
    labels = [item["label"] for item in revisions_of(client, project)]
    assert "Applied a template" in labels


def test_the_history_is_capped(client, monkeypatch):
    signed_in(client)
    no_throttle(monkeypatch)
    from app.services import revisions

    monkeypatch.setattr(revisions, "KEEP", 5)
    project = make_project(client)
    for index in range(12):
        client.patch(f"/api/projects/{project}", json={"title": f"Take {index}"})
    assert len(revisions_of(client, project)) == 5


def test_the_cap_keeps_the_newest(client, monkeypatch):
    signed_in(client)
    no_throttle(monkeypatch)
    from app.services import revisions

    monkeypatch.setattr(revisions, "KEEP", 3)
    project = make_project(client)
    for index in range(8):
        client.patch(f"/api/projects/{project}", json={"title": f"Take {index}"})
        time.sleep(0.01)

    newest = revisions_of(client, project)[0]["id"]
    restored = client.post(
        f"/api/projects/{project}/revisions/{newest}/restore"
    ).json()["project"]
    assert restored["title"] == "Take 6"


# --------------------------------------------------------------------------
# What the entries say
# --------------------------------------------------------------------------


@pytest.mark.parametrize("update,expected", [
    ({"title": "New"}, "Renamed"),
    ({"clip_start": 5.0}, "Moved the clip"),
    ({"aspect_ratio": "1:1"}, "Changed the shape"),
    ({"scene": {"cuts": [{"start": 1, "end": 2}]}}, "Cut the transcript"),
    ({"scene": {"accent": "#ff0000"}}, "Changed the design"),
])
def test_the_label_says_what_happened(client, monkeypatch, update, expected):
    """A diff of the scene JSON is a wall of coordinates; this is what you did."""
    signed_in(client)
    no_throttle(monkeypatch)
    project = make_project(client)
    client.patch(f"/api/projects/{project}", json=update)
    assert revisions_of(client, project)[0]["label"] == expected


def test_adding_a_layer_says_so(client, monkeypatch):
    signed_in(client)
    no_throttle(monkeypatch)
    project = make_project(client)
    client.patch(f"/api/projects/{project}", json={
        "scene": {"layers": [{"id": "a", "type": "text", "x": 0, "y": 0,
                              "width": 10, "height": 10}]},
    })
    assert revisions_of(client, project)[0]["label"] == "Changed the layers"


# --------------------------------------------------------------------------
# Restoring
# --------------------------------------------------------------------------


def test_restoring_brings_back_the_whole_state(client, monkeypatch):
    signed_in(client)
    no_throttle(monkeypatch)
    project = make_project(client)
    client.patch(f"/api/projects/{project}", json={
        "title": "Before", "clip_start": 10.0, "clip_end": 40.0,
        "scene": {"accent": "#123456"},
    })
    client.patch(f"/api/projects/{project}", json={
        "title": "After", "clip_start": 90.0, "clip_end": 120.0,
        "scene": {"accent": "#abcdef"},
    })

    target = [r for r in revisions_of(client, project)][0]["id"]
    restored = client.post(
        f"/api/projects/{project}/revisions/{target}/restore"
    ).json()["project"]
    assert restored["title"] == "Before"
    assert restored["clip_start"] == 10.0
    assert restored["scene"]["accent"] == "#123456"


def test_restoring_is_itself_undoable(client, monkeypatch):
    """Reaching for history and landing somewhere worse is not a one-way door."""
    signed_in(client)
    no_throttle(monkeypatch)
    project = make_project(client, "One")
    client.patch(f"/api/projects/{project}", json={"title": "Two"})

    before = len(revisions_of(client, project))
    target = revisions_of(client, project)[0]["id"]
    client.post(f"/api/projects/{project}/revisions/{target}/restore")

    history = revisions_of(client, project)
    assert len(history) == before + 1
    assert history[0]["label"] == "Before restoring"

    undo = history[0]["id"]
    back = client.post(
        f"/api/projects/{project}/revisions/{undo}/restore"
    ).json()["project"]
    assert back["title"] == "Two"


def test_an_unknown_revision_is_not_found(client):
    signed_in(client)
    project = make_project(client)
    assert client.post(
        f"/api/projects/{project}/revisions/nope/restore"
    ).status_code == 404


def test_a_revision_from_another_project_is_refused(client, monkeypatch):
    """Otherwise one clip's history could overwrite another clip."""
    signed_in(client)
    no_throttle(monkeypatch)
    first = make_project(client, "First")
    second = make_project(client, "Second")
    client.patch(f"/api/projects/{first}", json={"title": "First edited"})

    stolen = revisions_of(client, first)[0]["id"]
    assert client.post(
        f"/api/projects/{second}/revisions/{stolen}/restore"
    ).status_code == 404


# --------------------------------------------------------------------------
# Whose history it is
# --------------------------------------------------------------------------


def test_another_account_cannot_read_the_history(client, monkeypatch):
    signed_in(client)
    no_throttle(monkeypatch)
    project = make_project(client)
    client.patch(f"/api/projects/{project}", json={"title": "Mine"})

    register_second_user(client, "friend")
    assert client.get(f"/api/projects/{project}/revisions").status_code == 404


def test_another_account_cannot_restore(client, monkeypatch):
    signed_in(client)
    no_throttle(monkeypatch)
    project = make_project(client)
    client.patch(f"/api/projects/{project}", json={"title": "Mine"})
    revision = revisions_of(client, project)[0]["id"]

    register_second_user(client, "friend")
    assert client.post(
        f"/api/projects/{project}/revisions/{revision}/restore"
    ).status_code == 404


def test_signing_in_is_required(client):
    signed_in(client)
    project = make_project(client)
    client.post("/api/auth/logout")
    assert client.get(f"/api/projects/{project}/revisions").status_code == 401


# --------------------------------------------------------------------------
# Not being in the way
# --------------------------------------------------------------------------


def test_a_failure_to_record_history_does_not_fail_the_edit(client, monkeypatch):
    """History is a convenience; losing it must never lose the change."""
    signed_in(client)
    project = make_project(client)

    from app.services import revisions

    def boom(*args, **kwargs):
        raise RuntimeError("the history table is on fire")

    monkeypatch.setattr(revisions, "state_of", boom)
    response = client.patch(f"/api/projects/{project}", json={"title": "Saved anyway"})
    assert response.status_code == 200
    assert response.json()["project"]["title"] == "Saved anyway"


def test_deleting_a_project_takes_its_history(client, monkeypatch):
    signed_in(client)
    no_throttle(monkeypatch)
    project = make_project(client)
    client.patch(f"/api/projects/{project}", json={"title": "Doomed"})

    from app.db.models import ProjectRevision
    from app.db.session import SessionLocal

    client.delete(f"/api/projects/{project}")
    with SessionLocal() as db:
        left = db.query(ProjectRevision).filter(
            ProjectRevision.project_id == project
        ).count()
    assert left == 0
