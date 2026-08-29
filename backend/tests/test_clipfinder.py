"""Suggesting clips worth posting.

The slow part of making these is scrubbing an episode for the thirty seconds
that will travel. These are the signals that stand in for that judgement, and
the properties they have to hold.
"""

from __future__ import annotations

from app.services.clipfinder import (
    MAX_SECONDS,
    MIN_SECONDS,
    find,
    suggested_title,
)
from tests.test_api import create_test_client, register_second_user


def speech(text: str, start: float = 0.0, rate: float = 2.8) -> list[dict]:
    """Words at a plausible conversational pace."""
    words = []
    cursor = start
    step = 1.0 / rate
    for token in text.split():
        words.append({"text": token, "start": round(cursor, 3),
                      "end": round(cursor + step * 0.8, 3)})
        cursor += step
    return words


def transcript(words: list[dict]) -> dict:
    if not words:
        return {"language": "en", "duration": 0.0, "segments": []}
    return {
        "language": "en",
        "duration": words[-1]["end"] + 1,
        "segments": [{"id": 1, "speaker": "s", "start": words[0]["start"],
                      "end": words[-1]["end"], "text": " ".join(w["text"] for w in words),
                      "words": words}],
    }


# A passage long enough to yield several candidates.
LONG = (
    "The thing nobody tells you about moving abroad is how ordinary it feels. "
    "You expect fireworks and instead you get a Tuesday. "
    "I used to think the hard part would be the paperwork and the language. "
    "It turns out the hard part is that nothing feels like an occasion any more. "
    "Everyone asks whether I miss home and I never know how to answer that. "
    "The truth is I miss a version of it that stopped existing years ago. "
    "So what do you actually do with that feeling when it arrives. "
    "Honestly you just make dinner and get on with the evening."
)


# --------------------------------------------------------------------------
# Shape of the output
# --------------------------------------------------------------------------


def test_suggestions_come_back_best_first():
    clips = find(transcript(speech(LONG)))
    assert clips
    scores = [clip["score"] for clip in clips]
    assert scores == sorted(scores, reverse=True)


def test_every_suggestion_is_a_postable_length():
    for clip in find(transcript(speech(LONG))):
        assert MIN_SECONDS <= clip["duration"] <= MAX_SECONDS, clip


def test_suggestions_do_not_all_cover_the_same_moment():
    """Ten cuts of one moment is not ten choices."""
    clips = find(transcript(speech(LONG)), limit=5)
    for index, clip in enumerate(clips):
        for other in clips[index + 1:]:
            overlap = min(clip["end"], other["end"]) - max(clip["start"], other["start"])
            shorter = min(clip["duration"], other["duration"])
            assert overlap / shorter <= 0.40, (clip["start"], other["start"])


def test_every_suggestion_explains_itself():
    """A suggestion you cannot interrogate is one you cannot trust."""
    for clip in find(transcript(speech(LONG))):
        assert clip["reasons"] or clip["warnings"], clip
        assert clip["title"]


def test_the_limit_is_respected():
    assert len(find(transcript(speech(LONG)), limit=2)) <= 2


def test_clip_boundaries_line_up_with_real_words():
    words = speech(LONG)
    starts = [w["start"] for w in words]
    ends = [w["end"] for w in words]
    for clip in find(transcript(words)):
        # Output is rounded to centiseconds, so match to that.
        assert any(abs(clip["start"] - s) < 0.01 for s in starts), clip
        assert any(abs(clip["end"] - e) < 0.01 for e in ends), clip


# --------------------------------------------------------------------------
# The signals
# --------------------------------------------------------------------------


def test_a_clip_opening_on_a_dangling_pronoun_is_penalised():
    """"It was the best thing" is missing whatever "it" was."""
    from app.services.clipfinder import Candidate, score

    hooked = Candidate(0, 35, "The thing nobody tells you is that it gets easier.")
    dangling = Candidate(0, 35, "It gets easier after the first year of living there.")
    score(hooked, [], [], 100)
    score(dangling, [], [], 100)
    assert hooked.score > dangling.score
    assert any("refers back" in w for w in dangling.warnings)


def test_an_unfinished_sentence_is_penalised():
    from app.services.clipfinder import Candidate, score

    whole = Candidate(0, 35, "I moved abroad and it was harder than I expected.")
    cut = Candidate(0, 35, "I moved abroad and it was harder than")
    score(whole, [], [], 100)
    score(cut, [], [], 100)
    assert whole.score > cut.score
    assert any("full stop" in w for w in cut.warnings)


def test_filler_heavy_speech_is_penalised():
    from app.services.clipfinder import Candidate, score

    clean = Candidate(0, 20, "The reason it works is that nobody expects it.")
    filler = Candidate(0, 20, "So um like you know I mean it sort of um works.")
    score(clean, [], [], 100)
    score(filler, [], [], 100)
    assert clean.score > filler.score


def test_energetic_audio_lifts_a_clip():
    from app.services.clipfinder import Candidate, score

    text = "The thing nobody tells you is that it gets easier."
    quiet = Candidate(0, 35, text)
    loud = Candidate(0, 35, text)
    score(quiet, [], [0.05] * 100, 100)
    score(loud, [], [0.8] * 100, 100)
    assert loud.score > quiet.score
    assert any("energy" in r for r in loud.reasons)


def test_dynamic_audio_lifts_a_clip():
    from app.services.clipfinder import Candidate, score

    text = "The thing nobody tells you is that it gets easier."
    flat = Candidate(0, 35, text)
    varied = Candidate(0, 35, text)
    score(flat, [], [0.5] * 100, 100)
    score(varied, [], [0.1, 0.9] * 50, 100)
    assert varied.score > flat.score


def test_a_clean_entry_and_exit_are_rewarded():
    from app.services.clipfinder import Candidate, score

    words = [{"text": "x", "start": 0.0, "end": 1.0},
             {"text": "y", "start": 5.0, "end": 6.0},
             {"text": "z", "start": 10.0, "end": 11.0}]
    candidate = Candidate(5.0, 6.0, "y.")
    score(candidate, words, [], 20)
    assert "clean entry" in candidate.reasons
    assert "clean exit" in candidate.reasons


# --------------------------------------------------------------------------
# Edge cases
# --------------------------------------------------------------------------


def test_a_transcript_with_no_words_yields_nothing():
    assert find({"segments": []}) == []
    assert find({"language": "en", "duration": 0, "segments": []}) == []


def test_audio_too_short_to_clip_yields_nothing():
    assert find(transcript(speech("Hello there friend."))) == []


def test_a_transcript_without_word_timings_yields_nothing():
    plain = {"language": "en", "duration": 60,
             "segments": [{"id": 1, "speaker": "s", "start": 0, "end": 60,
                           "text": "no timings", "words": []}]}
    assert find(plain) == []


def test_titles_are_short_and_capitalised():
    assert suggested_title("the thing nobody tells you about it is simple.") \
        .startswith("The thing")
    assert len(suggested_title(LONG)) < 80
    assert suggested_title("") == "Untitled clip"


def test_titles_do_not_end_in_punctuation():
    assert not suggested_title("this is a whole sentence.").endswith(".")


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def signed_in(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})
    return client


def seed_media(client, words: list[dict] | None = None):
    """A media row carrying a transcript, without running a real import."""
    import json as jsonlib

    from app.db.models import MediaAsset, User
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        owner = db.query(User).first()
        asset = MediaAsset(
            owner_id=owner.id, original_name="ep.mp3", stored_name="ep.mp3",
            content_type="audio/mpeg", size_bytes=1, duration_seconds=120.0,
            # `or` would swallow an intentionally empty transcript.
            transcript_json=jsonlib.dumps(
                transcript(speech(LONG) if words is None else words)
            ),
        )
        db.add(asset)
        db.commit()
        return asset.id


def test_the_endpoint_returns_suggestions(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    media_id = seed_media(client)
    body = client.get(f"/api/media/{media_id}/clips").json()
    assert body["ready"] is True
    assert body["clips"]
    assert all("title" in clip for clip in body["clips"])


def test_the_endpoint_says_so_when_there_is_no_transcript(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    media_id = seed_media(client, words=[])
    body = client.get(f"/api/media/{media_id}/clips").json()
    assert body["ready"] is False
    assert body["clips"] == []
    assert body["reason"]


def test_a_transcript_with_nothing_worth_clipping_says_so(monkeypatch, tmp_path):
    """Different from "not ready": there is a transcript, it just has no clip.

    The UI has to tell these apart — one is worth waiting for and the other is
    not.
    """
    client = signed_in(monkeypatch, tmp_path)
    media_id = seed_media(client, words=speech("Hi there."))
    body = client.get(f"/api/media/{media_id}/clips").json()
    assert body["ready"] is True
    assert body["clips"] == []
    assert "self-contained" in body["reason"]


def test_the_limit_is_validated(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    media_id = seed_media(client)
    assert client.get(f"/api/media/{media_id}/clips?limit=0").status_code == 400
    assert client.get(f"/api/media/{media_id}/clips?limit=99").status_code == 400


def test_someone_elses_media_is_not_found(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    media_id = seed_media(client)
    client.post("/api/auth/logout")
    register_second_user(client, "guest", "another-password")
    assert client.get(f"/api/media/{media_id}/clips").status_code == 404
