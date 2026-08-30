"""Trivia for the progress bar: never blocks, never fails, always answers."""

from __future__ import annotations

import pytest

from app.services import facts
from tests.test_api import create_test_client


@pytest.fixture()
def client(monkeypatch, tmp_path):
    with create_test_client(monkeypatch, tmp_path) as test_client:
        yield test_client


def login(client) -> None:
    client.post("/api/bootstrap", json={
        "username": "owner", "password": "Passw0rd!enough", "display_name": "Owner",
    })


def test_bundled_lines_serve_when_the_internet_does_not(monkeypatch):
    monkeypatch.setattr(facts, "fetch_one", lambda: None)
    pool = facts.FactPool()
    got = pool.sample(5)
    assert len(got) == 5
    assert all(f in facts.BUNDLED for f in got)
    assert len(set(got)) == 5


def test_fetched_facts_are_preferred(monkeypatch):
    lines = iter([f"fact {i}" * 2 for i in range(10)] + [None])
    monkeypatch.setattr(facts, "fetch_one", lambda: next(lines))
    pool = facts.FactPool()
    pool._fill()
    got = pool.sample(3)
    assert all(f.startswith("fact") for f in got)


def test_odd_payloads_are_rejected(monkeypatch):
    class Response:
        def __init__(self, body):
            self.body = body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return self.body

    monkeypatch.setattr(facts.urllib.request, "urlopen", lambda *a, **k: Response(b'{"text": "<script>x</script> is a fact"}'))
    assert facts.fetch_one() is None
    monkeypatch.setattr(facts.urllib.request, "urlopen", lambda *a, **k: Response(b'{"text": "Otters hold hands while they sleep."}'))
    assert facts.fetch_one() == "Otters hold hands while they sleep."


def test_the_route_needs_a_session_and_returns_a_list(client, monkeypatch):
    monkeypatch.setattr(facts, "fetch_one", lambda: None)
    assert client.get("/api/facts").status_code == 401
    login(client)
    r = client.get("/api/facts", params={"n": 4})
    assert r.status_code == 200
    assert len(r.json()["facts"]) == 4
    # Capped, so nobody asks for a thousand.
    assert len(client.get("/api/facts", params={"n": 500}).json()["facts"]) <= 30
