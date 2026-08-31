"""Every API timestamp names its zone."""

from __future__ import annotations


def test_serialized_timestamps_carry_utc(monkeypatch, tmp_path):
    from tests.test_api import create_test_client

    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "zed", "password": "Passw0rd!enough"})
    me = client.get("/api/me").json()["user"]
    assert me["created_at"].endswith("+00:00") or me["created_at"].endswith("Z")

    project = client.post("/api/projects", json={"title": "tz check"}).json()["project"]
    assert project["created_at"].endswith("+00:00") or project["created_at"].endswith("Z")
    assert project["updated_at"].endswith("+00:00") or project["updated_at"].endswith("Z")
