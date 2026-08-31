"""The aspect switch carries the design; transcripts download in one click."""

from __future__ import annotations


def make(client):
    client.post("/api/bootstrap", json={"username": "bluep", "password": "Passw0rd!enough"})


def test_aspect_switch_remaps_and_survives(monkeypatch, tmp_path):
    from tests.test_api import create_test_client

    client = create_test_client(monkeypatch, tmp_path)
    make(client)
    project = client.post("/api/projects", json={"title": "shape"}).json()["project"]
    assert project["aspect_ratio"] == "9:16"

    switched = client.post(f"/api/projects/{project['id']}/aspect/16:9").json()["project"]
    assert switched["aspect_ratio"] == "16:9"
    # The guide follows the shape (16:9 -> youtube per RATIO_PRESETS).
    assert switched["scene"].get("platform") in (None, "youtube")

    bad = client.post(f"/api/projects/{project['id']}/aspect/2:1")
    assert bad.status_code == 400


def test_transcript_downloads(monkeypatch, tmp_path):
    from tests.test_api import create_test_client

    client = create_test_client(monkeypatch, tmp_path)
    make(client)
    upload = client.post(
        "/api/media/upload", files={"file": ("ep.mp3", b"ID3fake", "audio/mpeg")}
    ).json()["media"]

    from app.db.models import MediaAsset
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        row = db.get(MediaAsset, upload["id"])
        row.transcript_json = (
            '{"segments": [{"id": 1, "start": 0.0, "end": 1.5, "text": "hello"},'
            ' {"id": 2, "start": 1.5, "end": 3.0, "text": "there"}]}'
        )
        db.commit()

    srt = client.get(f"/api/media/{upload['id']}/transcript.srt")
    assert srt.status_code == 200 and "00:00:01" in srt.text and "hello" in srt.text
    assert "attachment" in srt.headers["content-disposition"]
    vtt = client.get(f"/api/media/{upload['id']}/transcript.vtt")
    assert vtt.text.startswith("WEBVTT")
    txt = client.get(f"/api/media/{upload['id']}/transcript.txt")
    assert txt.text == "hello\nthere\n"
    assert client.get(f"/api/media/{upload['id']}/transcript.doc").status_code == 400


def test_update_rejects_an_unknown_aspect_ratio(monkeypatch, tmp_path):
    from tests.test_api import create_test_client

    client = create_test_client(monkeypatch, tmp_path)
    make(client)
    project = client.post("/api/projects", json={"title": "shape guard"}).json()["project"]
    ok = client.patch(f"/api/projects/{project['id']}", json={"aspect_ratio": "1:1"})
    assert ok.status_code == 200 and ok.json()["project"]["aspect_ratio"] == "1:1"
    bad = client.patch(f"/api/projects/{project['id']}", json={"aspect_ratio": "banana"})
    assert bad.status_code == 400
    # The bad value did not land.
    assert client.get(f"/api/projects/{project['id']}").json()["project"]["aspect_ratio"] == "1:1"
