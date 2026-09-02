"""A retyped transcript line must be what the captions say.

The transcript editor changes a segment's text; Whisper's word timings stay
behind. Captions were built from the words alone, so a corrected name went
into the transcript and never into the video.
"""

from __future__ import annotations

from app.services.transcription import caption_lines, edited_words


def _segment(text: str, words: list[str], start: float = 0.0) -> dict:
    built = []
    cursor = start
    for word in words:
        built.append({"text": word, "start": round(cursor, 3), "end": round(cursor + 0.4, 3)})
        cursor += 0.4
    return {
        "id": 1, "speaker": "Speaker 1", "start": start, "end": round(cursor, 3),
        "text": text, "words": built,
    }


def test_untouched_lines_keep_their_words():
    segment = _segment("hello there Afiya", ["hello", " there", " Afiya"])
    assert edited_words(segment) is segment["words"]


def test_a_corrected_word_takes_its_timing():
    segment = _segment("hello there Afiya", ["hello", " there", " Afia"])
    words = edited_words(segment)
    assert [w["text"] for w in words] == ["hello", "there", "Afiya"]
    # Same moments, new spelling.
    assert [w["start"] for w in words] == [w["start"] for w in segment["words"]]
    lines = caption_lines({"segments": [segment]}, 0.0, 10.0)
    assert lines[0]["text"] == "hello there Afiya"


def test_a_rewritten_line_burns_in_whole():
    # Three words became five: no timing can be trusted per word, so the line
    # is shown as one caption rather than mis-highlighted.
    segment = _segment("well hello there my friend", ["hello", " there", " friend"])
    assert edited_words(segment) == []
    lines = caption_lines({"segments": [segment]}, 0.0, 10.0)
    assert len(lines) == 1
    assert lines[0]["text"] == "well hello there my friend"
    assert "words" not in lines[0]
