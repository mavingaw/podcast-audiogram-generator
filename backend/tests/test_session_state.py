"""The cold-load session check answers 200 either way."""

from __future__ import annotations

import pytest

from tests.test_api import create_test_client


@pytest.fixture()
def client(monkeypatch, tmp_path):
    with create_test_client(monkeypatch, tmp_path) as test_client:
        yield test_client


def test_signed_out_is_a_200_with_no_user(client):
    r = client.get("/api/session")
    assert r.status_code == 200
    assert r.json() == {"user": None}


def test_signed_in_returns_the_user(client):
    client.post("/api/bootstrap", json={
        "username": "owner", "password": "Passw0rd!enough", "display_name": "Owner",
    })
    r = client.get("/api/session")
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "owner"
