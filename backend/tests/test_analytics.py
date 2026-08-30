"""Did anyone watch the thing you sent?"""

from __future__ import annotations

import pytest

from tests.test_api import create_test_client, register_second_user


@pytest.fixture()
def client(monkeypatch, tmp_path):
    with create_test_client(monkeypatch, tmp_path) as test_client:
        yield test_client


def login(client) -> None:
    client.post("/api/bootstrap", json={
        "username": "owner", "password": "Passw0rd!enough", "display_name": "Owner",
    })


def shared_project(client) -> tuple[dict, str]:
    from app.api.routes import settings

    project = client.post("/api/projects", json={"title": "The good bit"}).json()["project"]
    out = settings.outputs_dir / project["id"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "audiogram.mp4").write_bytes(b"\x00" * 4096)
    token = client.post(f"/api/projects/{project['id']}/share").json()["token"]
    return project, token


def test_opens_and_plays_are_counted(client):
    login(client)
    project, token = shared_project(client)
    assert client.get("/api/analytics").json()["totals"] == {"views": 0, "plays": 0, "links_live": 1}

    client.post("/api/auth/logout")
    client.get(f"/s/{token}")
    client.get(f"/s/{token}")
    client.get(f"/s/{token}/video.mp4")
    # Seeking into the middle is not another play.
    client.get(f"/s/{token}/video.mp4", headers={"Range": "bytes=2048-4095"})

    client.post("/api/auth/login", json={"username": "owner", "password": "Passw0rd!enough"})
    data = client.get("/api/analytics").json()
    assert data["totals"] == {"views": 2, "plays": 1, "links_live": 1}
    row = data["clips"][0]
    assert row["title"] == "The good bit" and row["views"] == 2 and row["plays"] == 1
    assert row["link_live"] is True and row["last"]


def test_only_your_own_numbers_show(client):
    login(client)
    _, token = shared_project(client)
    client.post("/api/auth/logout")
    client.get(f"/s/{token}")
    register_second_user(client, "friend")
    assert client.get("/api/analytics").json()["totals"]["views"] == 0
