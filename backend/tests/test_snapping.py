"""Keeping clip edges off the middle of a word.

A clip that opens on "-portant" is the most obvious sign it was cut by dragging
a handle rather than by listening.
"""

from __future__ import annotations

from app.services.snapping import TOLERANCE, snap, words_of
from tests.test_api import create_test_client, register_second_user


def transcript(spans: list[tuple[str, float, float]]) -> dict:
    words = [{"text": t, "start": s, "end": e} for t, s, e in spans]
    return {
        "language": "en",
        "duration": words[-1]["end"] + 1 if words else 0,
        "segments": [{"id": 1, "speaker": "s", "start": 0, "end": 0, "text": "", "words": words}],
    }


# "the thing" then a pause, then "nobody tells you".
SPEECH = transcript([
    ("the", 1.0, 1.3),
    ("thing", 1.3, 1.8),
    ("nobody", 2.6, 3.1),
    ("tells", 3.1, 3.4),
    ("you", 3.4, 3.7),
])


# --------------------------------------------------------------------------
# The core behaviour
# --------------------------------------------------------------------------


def test_a_start_inside_a_word_takes_the_whole_word():
    result = snap(SPEECH, 1.5, 3.2)
    # 1.5 is inside "thing" (1.3-1.8), so the clip starts at its beginning.
    assert result.start <= 1.3
    assert result.moved


def test_an_end_inside_a_word_finishes_it():
    result = snap(SPEECH, 1.0, 3.2)
    # 3.2 is inside "tells" (3.1-3.4), so the clip runs to its end.
    assert result.end == 3.4


def test_edges_already_in_a_gap_are_left_alone():
    """Where in the silence is the editor's choice, not ours."""
    result = snap(SPEECH, 2.0, 3.9)
    assert (result.start, result.end) == (2.0, 3.9)
    assert not result.moved


def test_an_edge_exactly_on_a_word_boundary_is_left_alone():
    result = snap(SPEECH, 1.0, 3.7)
    assert (result.start, result.end) == (1.0, 3.7)
    assert not result.moved


def test_snapping_is_idempotent():
    """Snapping twice must not keep walking the boundary backwards.

    It did: the lead-in padding pushed a start into the *previous* word whenever
    speech was contiguous, so each pass ate another word.
    """
    once = snap(SPEECH, 1.5, 3.2)
    twice = snap(SPEECH, once.start, once.end)
    assert (twice.start, twice.end) == (once.start, once.end)
    assert not twice.moved


def test_the_lead_in_only_uses_silence_that_exists():
    """"the" ends exactly where "thing" begins, so there is nothing to pad into."""
    result = snap(SPEECH, 1.5, 3.7)
    assert result.start >= 1.3


def test_a_word_with_real_silence_before_it_gets_its_lead_in():
    result = snap(SPEECH, 2.8, 3.7)
    # "nobody" starts at 2.6 with silence from 1.8, so a little padding is free.
    assert 2.5 <= result.start < 2.6


def test_a_word_too_long_to_include_is_skipped_instead():
    """Rescuing the boundary must not drag the clip a long way backwards."""
    long_word = transcript([("elaborate", 0.0, 5.0), ("next", 5.2, 5.6)])
    # 4.9 is inside a five-second word; including it would move the start 4.9s.
    result = snap(long_word, 4.9, 5.6)
    assert result.start == 5.0


def test_an_edge_nowhere_near_a_word_is_untouched():
    result = snap(SPEECH, 0.2, 9.0)
    assert (result.start, result.end) == (0.2, 9.0)


def test_the_tolerance_is_a_fraction_of_a_second():
    assert 0.1 < TOLERANCE < 1.0


# --------------------------------------------------------------------------
# Not making things worse
# --------------------------------------------------------------------------


def test_a_clip_is_never_inverted_or_collapsed():
    tiny = transcript([("word", 1.0, 4.0)])
    result = snap(tiny, 3.8, 3.9)
    assert result.end > result.start


def test_snapping_respects_the_end_of_the_source():
    result = snap(SPEECH, 1.5, 3.2, duration=3.35)
    assert result.end <= 3.35


def test_no_transcript_means_no_change():
    assert snap(None, 1.0, 2.0).start == 1.0
    assert snap({}, 1.0, 2.0).end == 2.0
    assert not snap({"segments": []}, 1.0, 2.0).moved


def test_a_transcript_without_word_timings_means_no_change():
    plain = {"segments": [{"id": 1, "start": 0, "end": 9, "text": "x", "words": []}]}
    assert not snap(plain, 1.0, 2.0).moved


def test_malformed_word_timings_are_ignored():
    messy = {"segments": [{"words": [
        {"text": "a", "start": None, "end": 1.0},
        {"text": "b", "start": "oops", "end": 2.0},
        {"text": "c", "start": 3.0, "end": 3.5},
    ]}]}
    assert len(words_of(messy)) == 1
    # Snaps to the one usable word, with its lead-in.
    assert 2.9 <= snap(messy, 3.2, 4.0).start <= 3.0


def test_words_are_read_in_order_even_if_segments_are_not():
    out_of_order = {"segments": [
        {"words": [{"text": "b", "start": 5.0, "end": 5.5}]},
        {"words": [{"text": "a", "start": 1.0, "end": 1.5}]},
    ]}
    starts = [w["start"] for w in words_of(out_of_order)]
    assert starts == sorted(starts)


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def seeded(monkeypatch, tmp_path, data=SPEECH):
    import json as jsonlib
    import uuid

    client = create_test_client(monkeypatch, tmp_path)

    # Imported *after* the client, never before: create_test_client reloads the
    # db modules onto a per-test database, so a binding taken earlier writes to
    # the previous test's database and the app sees an empty one.
    from app.db.models import MediaAsset, User
    from app.db.session import SessionLocal

    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})
    with SessionLocal() as db:
        owner = db.query(User).first()
        asset = MediaAsset(
            owner_id=owner.id, original_name="ep.mp3",
            # stored_name is unique, and these tests seed more than one row.
            stored_name=f"{uuid.uuid4()}.mp3",
            content_type="audio/mpeg", size_bytes=1, duration_seconds=60.0,
            transcript_json=jsonlib.dumps(data) if data else None,
        )
        db.add(asset)
        db.commit()
        return client, asset.id


def test_the_endpoint_snaps(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    body = client.post(f"/api/media/{media_id}/snap", json={"start": 1.5, "end": 3.2}).json()
    assert body["moved"] is True
    assert body["end"] == 3.4


def test_the_endpoint_reports_when_nothing_moved(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    body = client.post(f"/api/media/{media_id}/snap", json={"start": 2.0, "end": 3.9}).json()
    assert body["moved"] is False


def test_the_endpoint_validates_the_range(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    assert client.post(
        f"/api/media/{media_id}/snap", json={"start": 5.0, "end": 2.0}
    ).status_code == 400


def test_media_without_a_transcript_snaps_to_itself(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path, data=None)
    body = client.post(f"/api/media/{media_id}/snap", json={"start": 1.5, "end": 3.2}).json()
    assert (body["start"], body["end"]) == (1.5, 3.2)


def test_someone_elses_media_is_not_found(monkeypatch, tmp_path):
    client, media_id = seeded(monkeypatch, tmp_path)
    client.post("/api/auth/logout")
    register_second_user(client, "guest", "another-password")
    assert client.post(
        f"/api/media/{media_id}/snap", json={"start": 1.0, "end": 2.0}
    ).status_code == 404
