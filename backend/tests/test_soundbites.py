"""Podcasting 2.0 soundbites: the podcaster's own choice of the best moment.

Every other clip suggestion in this application is a guess. A soundbite is not:
the person who made the episode marked the passage worth pulling out. So the
tests here are mostly about not losing them — to a parser that keeps one of
three, to a feed that writes the namespace the older way, or to our own ranking
deciding it knows better.
"""

from __future__ import annotations

import pytest

from app.services import feeds
from app.services.batching import suggestions_for


def feed_xml(items: str, namespace="https://podcastindex.org/namespace/1.0") -> bytes:
    return f"""<?xml version="1.0"?>
<rss version="2.0" xmlns:podcast="{namespace}">
  <channel><title>A show</title>{items}</channel>
</rss>""".encode()


def item(guid="g1", bites="") -> str:
    return f"""
    <item>
      <title>An episode</title>
      <guid>{guid}</guid>
      <enclosure url="https://example.com/{guid}.mp3" type="audio/mpeg" length="1000"/>
      {bites}
    </item>"""


BITE = '<podcast:soundbite startTime="73.5" duration="42.25">The best bit</podcast:soundbite>'


# --------------------------------------------------------------------------
# Reading them out of a feed
# --------------------------------------------------------------------------


def test_a_soundbite_is_read():
    found = feeds.soundbites_of(feed_xml(item(bites=BITE)))
    assert found["g1"][0] == feeds.Soundbite(73.5, 42.25, "The best bit")


def test_every_soundbite_survives():
    """feedparser keeps only the last one, which is why this is parsed by hand."""
    bites = (
        '<podcast:soundbite startTime="10" duration="20">One</podcast:soundbite>'
        '<podcast:soundbite startTime="90" duration="30">Two</podcast:soundbite>'
        '<podcast:soundbite startTime="200" duration="15">Three</podcast:soundbite>'
    )
    found = feeds.soundbites_of(feed_xml(item(bites=bites)))
    assert [bite.title for bite in found["g1"]] == ["One", "Two", "Three"]


def test_the_older_http_namespace_is_accepted():
    """The specification moved to https; plenty of feeds still declare http."""
    found = feeds.soundbites_of(
        feed_xml(item(bites=BITE), namespace="http://podcastindex.org/namespace/1.0")
    )
    assert found["g1"][0].start == 73.5


def test_a_soundbite_without_a_title_is_still_a_soundbite():
    found = feeds.soundbites_of(
        feed_xml(item(bites='<podcast:soundbite startTime="5" duration="10"/>'))
    )
    assert found["g1"][0].title == ""


def test_the_end_is_derived_from_the_duration():
    bite = feeds.Soundbite(73.5, 42.25)
    assert bite.end == pytest.approx(115.75)


def test_episodes_without_soundbites_are_absent_rather_than_empty():
    found = feeds.soundbites_of(feed_xml(item(guid="g1") + item(guid="g2", bites=BITE)))
    assert set(found) == {"g2"}


def test_a_soundbite_missing_its_times_is_ignored():
    """Both attributes are required; without them there is no clip to make."""
    bites = (
        '<podcast:soundbite duration="20">No start</podcast:soundbite>'
        '<podcast:soundbite startTime="10">No duration</podcast:soundbite>'
        '<podcast:soundbite startTime="x" duration="y">Not numbers</podcast:soundbite>'
        '<podcast:soundbite startTime="10" duration="0">Zero</podcast:soundbite>'
    )
    assert feeds.soundbites_of(feed_xml(item(bites=bites))) == {}


def test_a_different_namespace_is_not_ours():
    found = feeds.soundbites_of(
        feed_xml(item(bites=BITE), namespace="https://example.com/other/1.0")
    )
    assert found == {}


def test_a_broken_feed_yields_nothing_rather_than_raising():
    """An optional tag must never be able to fail an import."""
    assert feeds.soundbites_of(b"<rss><channel><item>") == {}
    assert feeds.soundbites_of(b"") == {}
    assert feeds.soundbites_of(None) == {}


# --------------------------------------------------------------------------
# Reaching the episode
# --------------------------------------------------------------------------


def test_episodes_carry_their_soundbites():
    import feedparser

    raw = feed_xml(item(bites=BITE))
    episodes = feeds.episodes_of(feedparser.parse(raw), raw)
    assert len(episodes) == 1
    assert episodes[0].soundbites[0].title == "The best bit"


def test_an_episode_without_them_has_an_empty_tuple():
    import feedparser

    raw = feed_xml(item())
    assert feeds.episodes_of(feedparser.parse(raw), raw)[0].soundbites == ()


def test_the_raw_document_is_optional():
    """Callers that have no raw XML still get episodes, just no soundbites."""
    import feedparser

    raw = feed_xml(item(bites=BITE))
    assert feeds.episodes_of(feedparser.parse(raw))[0].soundbites == ()


# --------------------------------------------------------------------------
# Being used
# --------------------------------------------------------------------------


class FakeMedia:
    peaks_json = None
    duration_seconds = 900.0
    transcript_json = None


def transcript_of() -> dict:
    """Real-shaped speech, repeated far enough out for a soundbite to sit past.

    The clipfinder wants word timings and sentences that end; a paragraph of
    the same synthetic line produces no candidates at all, which makes for a
    test that passes for the wrong reason.
    """
    from tests.test_batch import LONG, speech, transcript

    words = []
    cursor = 0.0
    for _ in range(6):
        block = speech(LONG)
        for word in block:
            words.append({
                "text": word["text"],
                "start": round(word["start"] + cursor, 3),
                "end": round(word["end"] + cursor, 3),
            })
        cursor = words[-1]["end"] + 0.6
    return transcript(words)


def test_a_soundbite_is_suggested_first():
    """It is the only suggestion that is not a guess."""
    picked = suggestions_for(
        FakeMedia(), transcript_of(), count=3,
        soundbites=[{"start": 300.0, "duration": 45.0, "title": "The best bit"}],
    )
    assert picked[0]["start"] == 300.0
    assert picked[0]["title"] == "The best bit"
    assert picked[0]["source"] == "soundbite"


def test_a_soundbite_says_why_it_was_chosen():
    picked = suggestions_for(
        FakeMedia(), transcript_of(), count=1,
        soundbites=[{"start": 300.0, "duration": 45.0, "title": ""}],
    )
    assert "podcaster" in picked[0]["reasons"][0]


def test_the_rest_of_the_slots_are_filled_by_the_usual_search():
    picked = suggestions_for(
        FakeMedia(), transcript_of(), count=3,
        soundbites=[{"start": 300.0, "duration": 45.0}],
    )
    assert len(picked) > 1
    assert any(item.get("source") != "soundbite" for item in picked[1:])


def test_more_soundbites_than_asked_for_are_trimmed():
    picked = suggestions_for(
        FakeMedia(), transcript_of(), count=2,
        soundbites=[{"start": t, "duration": 30.0} for t in (100.0, 400.0, 900.0)],
    )
    assert len(picked) == 2
    assert all(item["source"] == "soundbite" for item in picked)


def test_the_same_moment_is_not_suggested_twice():
    """A heuristic pick overlapping a soundbite is the same clip twice."""
    picked = suggestions_for(
        FakeMedia(), transcript_of(), count=4,
        soundbites=[{"start": 0.0, "duration": 60.0}],
    )
    for suggestion in picked[1:]:
        assert not (suggestion["start"] < 60.0 and suggestion["end"] > 0.0)


def test_nonsense_soundbites_are_skipped_not_fatal():
    picked = suggestions_for(
        FakeMedia(), transcript_of(), count=2,
        soundbites=[
            {"start": "soon", "duration": 30.0},
            {"duration": 30.0},
            {"start": 100.0, "duration": 0.2},
            {"start": 500.0, "duration": 40.0},
        ],
    )
    assert picked[0]["start"] == 500.0


def test_no_soundbites_behaves_exactly_as_before():
    with_none = suggestions_for(FakeMedia(), transcript_of(), count=3)
    with_empty = suggestions_for(FakeMedia(), transcript_of(), count=3, soundbites=[])
    assert [item["start"] for item in with_none] == [item["start"] for item in with_empty]
    assert len(with_none) > 1
