"""Delete puts a project in the trash; the trash can give it back."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


def make(client, title="A clip") -> dict:
    return client.post("/api/projects", json={"title": title}).json()["project"]


def test_delete_is_a_trip_to_the_trash_not_the_end(client):
    login(client)
    project = make(client)
    r = client.delete(f"/api/projects/{project['id']}").json()
    assert r["trashed"] is True and r["days"] == 7
    assert client.get("/api/projects").json()["projects"] == []
    trash = client.get("/api/projects", params={"trash": "1"}).json()["projects"]
    assert [p["id"] for p in trash] == [project["id"]]

    restored = client.post(f"/api/projects/{project['id']}/restore-from-trash").json()["project"]
    assert restored["id"] == project["id"]
    assert client.get("/api/projects", params={"trash": "1"}).json()["projects"] == []
    assert [p["id"] for p in client.get("/api/projects").json()["projects"]] == [project["id"]]


def test_deleting_from_the_trash_is_forever(client):
    login(client)
    project = make(client)
    client.delete(f"/api/projects/{project['id']}")
    r = client.delete(f"/api/projects/{project['id']}").json()
    assert r["trashed"] is False
    assert client.get("/api/projects", params={"trash": "1"}).json()["projects"] == []
    assert client.get(f"/api/projects/{project['id']}").status_code == 404


def test_forever_skips_the_trash_when_asked(client):
    login(client)
    project = make(client)
    client.delete(f"/api/projects/{project['id']}", params={"forever": "1"})
    assert client.get("/api/projects", params={"trash": "1"}).json()["projects"] == []


def test_the_trash_empties_itself_after_a_week(client):
    from app.db.models import Project
    from app.db.session import SessionLocal

    login(client)
    project = make(client)
    client.delete(f"/api/projects/{project['id']}")
    with SessionLocal() as db:
        row = db.get(Project, project["id"])
        row.deleted_at = datetime.now(timezone.utc) - timedelta(days=8)
        db.commit()
    assert client.get("/api/projects", params={"trash": "1"}).json()["projects"] == []
    assert client.get(f"/api/projects/{project['id']}").status_code == 404


def test_trashing_turns_share_links_off(client):
    from app.api.routes import settings

    login(client)
    project = make(client)
    out = settings.outputs_dir / project["id"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "audiogram.mp4").write_bytes(b"\x00" * 1024)
    token = client.post(f"/api/projects/{project['id']}/share").json()["token"]
    client.delete(f"/api/projects/{project['id']}")
    assert client.get(f"/s/{token}").status_code == 404
