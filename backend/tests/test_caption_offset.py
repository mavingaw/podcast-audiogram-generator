"""Nudging every caption against the audio.

Whisper's word timings are accurate to a few tens of milliseconds most of the
time, and occasionally not: a noisy passage, an overlapping speaker, a hard
accent. When a whole clip's captions sit consistently behind the voice there is
nothing to fix word by word and re-transcribing rarely helps, so there is one
number for it.

The thing to get right is that it changes *when a line is shown* and not *which
words the clip contains*. Shifting the transcript before the window is applied
would do the second, and the caption text itself would quietly change.
"""

from __future__ import annotations

import pytest

from app.services.jobs import _clip_captions, _shift_captions
from app.services.scene import parse as parse_scene


def speech() -> dict:
    """Four seconds of words, one every half second."""
    words = [
        {"start": round(index * 0.5, 2), "end": round(index * 0.5 + 0.4, 2),
         "text": f" word{index}"}
        for index in range(8)
    ]
    return {
        "duration": 4.0,
        "segments": [{
            "id": 1, "speaker": "Speaker 1", "start": 0.0, "end": 4.0,
            "text": " ".join(word["text"].strip() for word in words),
            "words": words,
        }],
    }


# --------------------------------------------------------------------------
# The scene
# --------------------------------------------------------------------------


def test_the_default_is_no_offset():
    assert parse_scene({}, 10.0).caption_offset == 0.0


def test_the_offset_is_read():
    assert parse_scene({"captionOffset": 0.35}, 10.0).caption_offset == 0.35
    assert parse_scene({"captionOffset": -0.2}, 10.0).caption_offset == -0.2


def test_an_absurd_offset_is_bounded():
    """Past a second the caption belongs to a different sentence."""
    assert parse_scene({"captionOffset": 30}, 10.0).caption_offset == 1.0
    assert parse_scene({"captionOffset": -30}, 10.0).caption_offset == -1.0


def test_nonsense_falls_back_to_none():
    assert parse_scene({"captionOffset": "late"}, 10.0).caption_offset == 0.0


# --------------------------------------------------------------------------
# The shift
# --------------------------------------------------------------------------


def test_no_offset_leaves_the_captions_untouched():
    captions = [{"start": 1.0, "end": 2.0, "text": "a"}]
    assert _shift_captions(captions, 0.0, 10.0) == captions


def test_a_positive_offset_shows_the_caption_later():
    shifted = _shift_captions([{"start": 1.0, "end": 2.0, "text": "a"}], 0.3, 10.0)
    assert shifted[0]["start"] == pytest.approx(1.3)
    assert shifted[0]["end"] == pytest.approx(2.3)


def test_a_negative_offset_shows_it_earlier():
    shifted = _shift_captions([{"start": 1.0, "end": 2.0, "text": "a"}], -0.3, 10.0)
    assert shifted[0]["start"] == pytest.approx(0.7)


def test_a_caption_pushed_before_the_start_is_clamped_not_dropped():
    """It still has words in it; showing it from 0 is right."""
    shifted = _shift_captions([{"start": 0.1, "end": 1.0, "text": "a"}], -0.5, 10.0)
    assert shifted[0]["start"] == 0.0
    assert shifted[0]["end"] == pytest.approx(0.5)


def test_a_caption_pushed_past_the_end_is_clamped():
    shifted = _shift_captions([{"start": 9.0, "end": 9.8, "text": "a"}], 0.5, 10.0)
    assert shifted[0]["end"] <= 10.0


def test_a_caption_pushed_entirely_outside_is_dropped():
    assert _shift_captions([{"start": 0.0, "end": 0.2, "text": "a"}], -1.0, 10.0) == []


def test_a_shifted_caption_never_ends_before_it_starts():
    for offset in (-1.0, -0.5, 0.5, 1.0):
        for caption in _shift_captions(
            [{"start": 9.5, "end": 9.9, "text": "a"}, {"start": 0.0, "end": 0.4, "text": "b"}],
            offset, 10.0,
        ):
            assert caption["end"] > caption["start"]


def test_word_timings_move_with_the_line():
    """Otherwise the karaoke highlight lights a word the line is not showing."""
    shifted = _shift_captions(
        [{"start": 1.0, "end": 2.0, "text": "a b",
          "words": [{"start": 1.0, "end": 1.4, "text": "a"},
                    {"start": 1.5, "end": 2.0, "text": "b"}]}],
        0.25, 10.0,
    )
    assert shifted[0]["words"][0]["start"] == pytest.approx(1.25)
    assert shifted[0]["words"][1]["end"] == pytest.approx(2.25)


# --------------------------------------------------------------------------
# Through the caption builder
# --------------------------------------------------------------------------


def test_the_offset_reaches_the_built_captions():
    plain = _clip_captions(speech(), 0.0, 4.0)
    late = _clip_captions(speech(), 0.0, 4.0, offset=0.3)
    assert late[0]["start"] == pytest.approx(plain[0]["start"] + 0.3, abs=0.01)


def test_the_words_themselves_do_not_change():
    """The clip says the same thing; only when it is shown moves."""
    plain = " ".join(line["text"] for line in _clip_captions(speech(), 0.0, 4.0))
    late = " ".join(line["text"] for line in _clip_captions(speech(), 0.0, 4.0, offset=0.4))
    assert plain == late


def test_a_segment_without_word_timings_shifts_too():
    transcript = {"segments": [{"id": 1, "start": 1.0, "end": 3.0, "text": "no timings"}]}
    plain = _clip_captions(transcript, 0.0, 4.0)
    late = _clip_captions(transcript, 0.0, 4.0, offset=0.5)
    assert late[0]["start"] == pytest.approx(plain[0]["start"] + 0.5, abs=0.01)
