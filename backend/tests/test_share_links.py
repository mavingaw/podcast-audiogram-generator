"""A link to a finished clip that needs no account."""

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


def rendered_project(client) -> dict:
    # The fixture reloads the config module; read the settings the route uses.
    from app.api.routes import settings

    project = client.post("/api/projects", json={"title": "Winter: the good bit"}).json()["project"]
    out = settings.outputs_dir / project["id"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "audiogram.mp4").write_bytes(b"\x00" * 2048)
    return project


def test_a_link_opens_without_signing_in(client):
    login(client)
    project = rendered_project(client)
    link = client.post(f"/api/projects/{project['id']}/share").json()
    assert link["url"].endswith(f"/s/{link['token']}")
    client.post("/api/auth/logout")
    page = client.get(f"/s/{link['token']}")
    assert page.status_code == 200 and "Winter: the good bit" in page.text
    video = client.get(f"/s/{link['token']}/video.mp4")
    assert video.status_code == 200 and video.headers["content-type"].startswith("video/mp4")
    disposition = video.headers.get("content-disposition", "")
    assert "Winter" in disposition and ".mp4" in disposition


def test_the_same_link_comes_back_until_revoked(client):
    login(client)
    project = rendered_project(client)
    first = client.post(f"/api/projects/{project['id']}/share").json()["token"]
    second = client.post(f"/api/projects/{project['id']}/share").json()["token"]
    assert first == second
    assert client.delete(f"/api/projects/{project['id']}/share").json()["revoked"] == 1
    assert client.get(f"/s/{first}").status_code == 404
    third = client.post(f"/api/projects/{project['id']}/share").json()["token"]
    assert third != first


def test_an_unrendered_clip_cannot_be_shared(client):
    login(client)
    project = client.post("/api/projects", json={"title": "Not yet"}).json()["project"]
    assert client.post(f"/api/projects/{project['id']}/share").status_code == 409


def test_somebody_elses_project_cannot_be_shared(client):
    login(client)
    project = rendered_project(client)
    register_second_user(client, "friend")
    assert client.post(f"/api/projects/{project['id']}/share").status_code == 404


def test_a_made_up_token_is_a_404(client):
    bad = client.get("/s/not-a-real-token")
    assert bad.status_code == 404
    # A person opening the link in a browser gets a friendly page, not JSON.
    assert "text/html" in bad.headers["content-type"]
    assert "isn't available" in bad.text
    # The asset routes stay as they are (consumed by the <video> tag).
    assert client.get("/s/not-a-real-token/video.mp4").status_code == 404


def test_the_poster_is_a_recognised_output(client):
    login(client)
    project = rendered_project(client)
    # The fake mp4 cannot yield a frame; the route must answer 404, not 500.
    r = client.get(f"/api/projects/{project['id']}/outputs/poster.jpg")
    assert r.status_code == 404
