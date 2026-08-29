"""Attributing speech to people, and getting it onto the screen.

The detection itself is a model's problem; this covers the part that must be
right regardless of how good the model is — that labels can be corrected, that
corrections survive, and that a one-voice clip is untouched by any of it.
"""

from __future__ import annotations



import pytest

from app.services import speakers
from app.services.speakers import (
    MAX_SPEAKERS,
    assign,
    colour_for,
    is_multi_speaker,
    name_for,
    rename,
    segment_speaker,
    speaker_ids,
    summary,
)
from tests.test_api import create_test_client, register_second_user


def transcript(spans: list[tuple[float, float, int | None]]) -> dict:
    segments = []
    for index, (start, end, speaker) in enumerate(spans):
        segment = {"id": index, "start": start, "end": end, "text": f"line {index}"}
        if speaker is not None:
            segment["speaker_id"] = speaker
            segment["speaker"] = f"Speaker {speaker}"
        segments.append(segment)
    return {"language": "en", "duration": 60.0, "segments": segments}


def two() -> dict:
    """A fresh two-speaker transcript.

    A function rather than a constant: `dict(TWO)` is a shallow copy, so the
    segments list would be shared and one test's rename would leak into the next.
    """
    return transcript([(0.0, 5.0, 1), (5.0, 10.0, 2), (10.0, 15.0, 1)])


TWO = two()


# --------------------------------------------------------------------------
# Reading a transcript
# --------------------------------------------------------------------------


def test_speakers_are_listed_in_the_order_they_first_speak():
    assert speaker_ids(transcript([(0, 1, 2), (1, 2, 1), (2, 3, 2)])) == [2, 1]


def test_a_transcript_with_no_speakers_is_one_speaker():
    assert speaker_ids(transcript([(0, 1, None)])) == [1]
    assert speaker_ids({"segments": []}) == [1]
    assert speaker_ids(None) == [1]


def test_transcripts_written_before_speakers_existed_still_read():
    """Older transcripts have only the free-text label this app always set."""
    old = {"segments": [{"start": 0, "end": 1, "speaker": "Speaker 2", "text": "x"}]}
    assert segment_speaker(old["segments"][0]) == 2


def test_an_unparseable_speaker_is_speaker_one():
    """A missing attribution is not a broken transcript."""
    assert segment_speaker({"speaker": "somebody"}) == 1
    assert segment_speaker({"speaker_id": "nonsense"}) == 1
    assert segment_speaker({"speaker_id": 99}) == 1
    assert segment_speaker({}) == 1


def test_one_voice_is_not_multi_speaker():
    """The feature has to cost nothing when it is not being used."""
    assert is_multi_speaker(transcript([(0, 1, 1), (1, 2, 1)])) is False
    assert is_multi_speaker(TWO) is True


# --------------------------------------------------------------------------
# Colours
# --------------------------------------------------------------------------


def test_each_speaker_gets_a_distinct_colour():
    seen = {colour_for(n) for n in range(1, MAX_SPEAKERS + 1)}
    assert len(seen) == MAX_SPEAKERS


def test_colours_wrap_rather_than_failing():
    assert colour_for(MAX_SPEAKERS + 1) == colour_for(1)
    assert colour_for(0) == colour_for(1)


def test_the_first_speaker_gets_the_brand_accent():
    from app.services.scene import BRAND

    assert colour_for(1) == BRAND["blue"]


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------


def test_a_speaker_can_be_named():
    data = rename(two(), 1, "Marcus")
    assert name_for(data, 1) == "Marcus"
    assert data["segments"][0]["speaker"] == "Marcus"


def test_renaming_only_touches_that_speakers_segments():
    data = rename(two(), 2, "Guest")
    assert data["segments"][0]["speaker"] == "Speaker 1"
    assert data["segments"][1]["speaker"] == "Guest"


def test_an_empty_name_reverts_to_the_number():
    data = rename(rename(two(), 1, "Marcus"), 1, "   ")
    assert name_for(data, 1) == "Speaker 1"


def test_names_are_length_limited():
    data = rename(two(), 1, "x" * 200)
    assert len(name_for(data, 1)) <= 40


def test_naming_an_impossible_speaker_is_refused():
    with pytest.raises(ValueError):
        rename(two(), 0, "Nobody")
    with pytest.raises(ValueError):
        rename(two(), MAX_SPEAKERS + 1, "Nobody")


def test_a_name_survives_detection_running_again():
    """Re-running detection must not undo somebody's correction."""
    from app.services.diarization import Diarization, Turn, apply

    data = rename(
        transcript([(0.0, 5.0, 1), (5.0, 10.0, 2)]), 2, "Marcus"
    )
    apply(data, Diarization(turns=[Turn(0.0, 5.0, 1), Turn(5.0, 10.0, 2)], speaker_count=2))
    assert data["segments"][1]["speaker"] == "Marcus"


# --------------------------------------------------------------------------
# Assigning
# --------------------------------------------------------------------------


def test_a_range_reassigns_every_segment_it_covers():
    data = transcript([(0, 5, 1), (5, 10, 1), (10, 15, 1)])
    # 4.0-11.0 clips the first segment, covers the second, and clips the third.
    assert assign(data, 4.0, 11.0, 2) == 3
    assert [s["speaker_id"] for s in data["segments"]] == [2, 2, 2]


def test_segments_outside_the_range_are_untouched():
    data = transcript([(0, 5, 1), (5, 10, 1), (10, 15, 1)])
    assign(data, 5.0, 10.0, 2)
    assert [s["speaker_id"] for s in data["segments"]] == [1, 2, 1]


def test_assigning_reports_only_what_changed():
    data = transcript([(0, 5, 2), (5, 10, 1)])
    assert assign(data, 0.0, 20.0, 2) == 1


def test_assigning_applies_the_speakers_name():
    data = rename(transcript([(0, 5, 1)]), 2, "Marcus")
    assign(data, 0.0, 5.0, 2)
    assert data["segments"][0]["speaker"] == "Marcus"


def test_an_impossible_speaker_is_refused():
    with pytest.raises(ValueError):
        assign(two(), 0.0, 5.0, 99)


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------


def test_the_summary_counts_segments_per_speaker():
    rows = summary(two())
    assert [row["id"] for row in rows] == [1, 2]
    assert rows[0]["segments"] == 2
    assert rows[1]["segments"] == 1
    assert all(row["colour"] for row in rows)


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def seeded(monkeypatch, tmp_path, data=None):
    import json as jsonlib
    import uuid

    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})

    # After the reload, never before: see create_test_client's docstring.
    from app.db.models import MediaAsset, User
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        owner = db.query(User).first()
        asset = MediaAsset(
            owner_id=owner.id, original_name="ep.mp3",
            stored_name=f"{uuid.uuid4()}.mp3", content_type="audio/mpeg",
            size_bytes=1, duration_seconds=60.0,
            transcript_json=jsonlib.dumps(two() if data is None else data) if data is not False else None,
        )
        db.add(asset)
        db.commit()
        return client, asset.id


def test_the_endpoint_lists_speakers(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    body = client.get(f"/api/media/{media_id}/speakers").json()
    assert [row["id"] for row in body["speakers"]] == [1, 2]
    assert body["multi"] is True
    assert "ready" in body["detection"]


def test_the_endpoint_renames(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    body = client.post(
        f"/api/media/{media_id}/speakers/2/name", json={"name": "Marcus"}
    ).json()
    assert body["speakers"][1]["name"] == "Marcus"
    # And it persisted.
    again = client.get(f"/api/media/{media_id}/speakers").json()
    assert again["speakers"][1]["name"] == "Marcus"


def test_the_endpoint_assigns_a_range(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    body = client.post(
        f"/api/media/{media_id}/speakers/assign",
        json={"start": 0.0, "end": 20.0, "speaker_id": 2},
    ).json()
    assert body["changed"] == 2
    assert client.get(f"/api/media/{media_id}/speakers").json()["multi"] is False


def test_an_out_of_range_speaker_is_rejected(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    assert client.post(
        f"/api/media/{media_id}/speakers/assign",
        json={"start": 0.0, "end": 5.0, "speaker_id": 99},
    ).status_code == 422


def test_someone_elses_media_is_not_found(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    client.post("/api/auth/logout")
    register_second_user(client, "guest", "another-password")
    assert client.get(f"/api/media/{media_id}/speakers").status_code == 404
    assert client.post(
        f"/api/media/{media_id}/speakers/1/name", json={"name": "x"}
    ).status_code == 404


def test_detection_without_a_transcript_is_refused(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path, data=False)
    response = client.post(f"/api/media/{media_id}/speakers/detect", json={})
    # Either "not installed" or "transcribe first" — never a crash.
    assert response.status_code in (400, 503)
