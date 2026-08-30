"""Show notes from the transcript, with the model stubbed."""

from __future__ import annotations

import json

import pytest

from app.services import notes
from tests.test_api import create_test_client


@pytest.fixture()
def client(monkeypatch, tmp_path):
    with create_test_client(monkeypatch, tmp_path) as test_client:
        yield test_client


def login(client) -> None:
    client.post("/api/bootstrap", json={
        "username": "owner", "password": "Passw0rd!enough", "display_name": "Owner",
    })


FINAL = json.dumps({
    "titles": ["Growing pains", "Getting in shape", "Raquel's return"],
    "description": "A frank talk about growing up. It gets personal fast.",
    "highlights": ["The diagnosis story", "Vermont", "Community"],
    "keywords": ["Podcast", "growth"],
    "hashtags": ["adultish", "#podcast"],
})


def fake_complete(prompt, max_tokens=400, temperature=0.4):
    if "Reply with JSON" in prompt:
        return "Sure! Here you go:\n" + FINAL
    return "- The hosts discuss growing up\n- A story about Vermont\nshort"


def test_generate_reads_in_chunks_and_writes_up(monkeypatch):
    from app.services import llm

    monkeypatch.setattr(llm, "complete", fake_complete)
    transcript = {"segments": [{"text": "word " * 3000}]}
    result = notes.generate(transcript)
    assert result["titles"][0] == "Growing pains"
    assert result["description"].startswith("A frank talk")
    assert result["keywords"] == ["podcast", "growth"]
    assert result["hashtags"] == ["#adultish", "#podcast"]
    assert result["notes_taken"] >= 4  # two chunks x two usable notes


def test_the_route_runs_it_and_the_status_lands(client, monkeypatch):
    from app.services import llm

    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "complete", fake_complete)
    # Run the thread body inline so the test sees the result immediately.
    monkeypatch.setattr(notes.threading, "Thread",
                        lambda target, args, name, daemon: type("T", (), {
                            "start": lambda self: target(*args),
                            "is_alive": lambda self: False,
                        })())
    login(client)
    media = client.post("/api/media/upload", files={"file": ("ep.mp3", b"x", "audio/mpeg")}).json()["media"]
    assert client.post(f"/api/media/{media['id']}/notes").status_code == 409  # no transcript yet

    from app.db.models import MediaAsset
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        db.get(MediaAsset, media["id"]).transcript_json = json.dumps(
            {"segments": [{"text": "hello world " * 400}]}
        )
        db.commit()
    client.post(f"/api/media/{media['id']}/notes")
    state = client.get(f"/api/media/{media['id']}/notes").json()
    assert state["status"] == "done"
    assert state["result"]["titles"]


def test_no_model_is_a_plain_answer(client, monkeypatch):
    from app.services import llm

    monkeypatch.setattr(llm, "available", lambda: False)
    login(client)
    media = client.post("/api/media/upload", files={"file": ("ep.mp3", b"x", "audio/mpeg")}).json()["media"]
    from app.db.models import MediaAsset
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        db.get(MediaAsset, media["id"]).transcript_json = json.dumps({"segments": [{"text": "hi"}]})
        db.commit()
    r = client.post(f"/api/media/{media['id']}/notes")
    assert r.status_code == 409 and "model" in r.json()["detail"].lower()
