from __future__ import annotations

from app.services.transcription import (
    MAX_CAPTION_CHARS,
    MAX_CAPTION_SECONDS,
    caption_lines,
    choose_runtime,
)


def words(pairs: list[tuple[str, float, float]]) -> list[dict]:
    return [{"text": text, "start": start, "end": end} for text, start, end in pairs]


def transcript(segments: list[dict]) -> dict:
    return {"language": "en", "duration": 60.0, "segments": segments}


def sentence(text: str, start: float, per_word: float = 0.4) -> dict:
    """A segment whose words are evenly spaced, for predictable timings."""
    parts = text.split()
    built = []
    cursor = start
    for part in parts:
        built.append((part, round(cursor, 3), round(cursor + per_word, 3)))
        cursor += per_word
    return {
        "id": 1,
        "speaker": "Speaker 1",
        "start": start,
        "end": round(cursor, 3),
        "text": text,
        "words": words(built),
    }


# --------------------------------------------------------------------------
# Runtime selection
# --------------------------------------------------------------------------


def test_runtime_falls_back_to_cpu_when_gpu_is_not_wanted():
    runtime = choose_runtime(prefer_gpu=False)
    assert runtime.device == "cpu"
    assert runtime.compute_type == "int8"
    assert runtime.on_gpu is False


def test_unknown_model_size_falls_back_to_the_default():
    assert choose_runtime("enormous", prefer_gpu=False).model_size == "small"


def test_explicit_model_size_is_honoured():
    assert choose_runtime("tiny", prefer_gpu=False).model_size == "tiny"


# --------------------------------------------------------------------------
# Caption lines
# --------------------------------------------------------------------------


def test_caption_lines_respect_the_character_budget():
    long = "the thing nobody tells you about moving abroad is how ordinary it feels"
    lines = caption_lines(transcript([sentence(long, 0.0)]), 0.0, 60.0)
    assert lines
    for line in lines:
        assert len(line["text"]) <= MAX_CAPTION_CHARS + 12, line["text"]


def test_caption_lines_are_balanced_rather_than_greedy():
    """Greedy packing strands the remainder on a line of its own."""
    long = "one two three four five six seven eight nine ten eleven twelve thirteen"
    lines = caption_lines(transcript([sentence(long, 0.0)]), 0.0, 60.0)
    assert len(lines) > 1
    lengths = [len(line["text"]) for line in lines]
    # No line may be a scrap next to its neighbours.
    assert min(lengths) > max(lengths) / 3, lengths


def test_no_caption_line_is_instantaneous():
    long = "a b c d e f g h i j k l m n o p q r s t u v w x y z aa bb cc dd ee ff"
    for line in caption_lines(transcript([sentence(long, 0.0, 0.12)]), 0.0, 60.0):
        assert line["end"] - line["start"] >= 0.25, line


def test_caption_lines_are_relative_to_the_clip_start():
    segment = sentence("hello there friend", 10.0)
    lines = caption_lines(transcript([segment]), 10.0, 20.0)
    assert lines[0]["start"] == 0.0


def test_segments_outside_the_window_are_dropped():
    early = sentence("before the clip", 0.0)
    inside = sentence("inside the clip", 30.0)
    late = sentence("after the clip", 55.0)
    lines = caption_lines(transcript([early, inside, late]), 28.0, 34.0)
    assert lines
    assert all("before" not in line["text"] for line in lines)
    assert all("after" not in line["text"] for line in lines)


def test_long_pauses_split_lines_even_when_short_enough():
    """A line must not linger on screen just because it is under the char limit."""
    slow = sentence("this sentence is spoken very slowly indeed", 0.0, per_word=1.2)
    lines = caption_lines(transcript([slow]), 0.0, 60.0)
    for line in lines:
        assert line["end"] - line["start"] <= MAX_CAPTION_SECONDS * 2.2, line


def test_segments_without_word_timings_fall_back_to_whole_segment():
    plain = {
        "id": 1, "speaker": "Speaker 1", "start": 1.0, "end": 4.0,
        "text": "no word timings here", "words": [],
    }
    lines = caption_lines(transcript([plain]), 0.0, 10.0)
    assert lines == [{"start": 1.0, "end": 4.0, "text": "no word timings here"}]


def test_empty_transcript_produces_no_lines():
    assert caption_lines(transcript([]), 0.0, 10.0) == []
    assert caption_lines({"segments": []}, 0.0, 10.0) == []


def test_renderer_uses_word_timings_when_the_transcript_has_them():
    """The clip captions path must prefer word-level lines over raw segments."""
    from app.services.jobs import _clip_captions

    long = "one two three four five six seven eight nine ten eleven twelve"
    lines = _clip_captions(transcript([sentence(long, 0.0)]), 0.0, 30.0)
    assert len(lines) > 1
    assert all(len(line["text"]) <= MAX_CAPTION_CHARS + 12 for line in lines)


def test_renderer_falls_back_to_segments_without_word_timings():
    from app.services.jobs import _clip_captions

    plain = {
        "id": 1, "speaker": "Speaker 1", "start": 0.0, "end": 3.0,
        "text": "a whole segment", "words": [],
    }
    lines = _clip_captions(transcript([plain]), 0.0, 10.0)
    assert lines[0]["text"] == "a whole segment"


# --------------------------------------------------------------------------
# Rebuilding a line from word tokens
# --------------------------------------------------------------------------


def test_hyphenated_speech_is_not_pulled_apart():
    """Whisper splits "day-to-day" into "day", "-to", "-day"."""
    from app.services.transcription import join_words

    tokens = [{"text": t} for t in ("day", "-to", "-day", "is", "not")]
    assert join_words(tokens) == "day-to-day is not"


def test_whisper_leading_spaces_are_respected():
    from app.services.transcription import join_words

    assert join_words([{"text": " my"}, {"text": " actual"}, {"text": " day"}]) == "my actual day"


def test_punctuation_attaches_to_the_word_before_it():
    from app.services.transcription import join_words

    tokens = [{"text": t} for t in ("it", "'s", "fine", ",", "really", ".")]
    assert join_words(tokens) == "it's fine, really."


def test_stripped_tokens_still_get_spaces():
    """Transcripts recorded before this fix have no leading spaces at all."""
    from app.services.transcription import join_words

    assert join_words([{"text": "hello"}, {"text": "world"}]) == "hello world"


def test_empty_and_missing_tokens_are_skipped():
    from app.services.transcription import join_words

    assert join_words([{"text": ""}, {"text": "a"}, {}, {"text": "b"}]) == "a b"
    assert join_words([]) == ""


def test_caption_lines_use_the_same_joining():
    from app.services.transcription import caption_lines

    words = [
        {"text": "day", "start": 0.0, "end": 0.3},
        {"text": "-to", "start": 0.3, "end": 0.5},
        {"text": "-day", "start": 0.5, "end": 0.8},
    ]
    transcript = {
        "language": "en", "duration": 5.0,
        "segments": [{"id": 1, "speaker": "s", "start": 0.0, "end": 1.0,
                      "text": "day-to-day", "words": words}],
    }
    assert caption_lines(transcript, 0.0, 5.0)[0]["text"] == "day-to-day"


def test_a_line_never_begins_with_a_hyphen_or_punctuation():
    """Breaking between "day-to" and "-day" left a stray hyphen leading a line."""
    from app.services.transcription import caption_lines

    tokens = ["my", "actual", "day", "-to", "-day", "is", "not", "that", "interesting"]
    words = [
        {"text": token, "start": index * 0.3, "end": index * 0.3 + 0.3}
        for index, token in enumerate(tokens)
    ]
    transcript = {
        "language": "en", "duration": 10.0,
        "segments": [{"id": 1, "speaker": "s", "start": 0.0, "end": 3.0,
                      "text": " ".join(tokens), "words": words}],
    }
    lines = caption_lines(transcript, 0.0, 10.0, max_chars=16)
    assert len(lines) > 1
    for line in lines:
        assert line["text"][0] not in ",.!?;:-'", line
    # And the hyphenated word survived intact on one line.
    assert any("day-to-day" in line["text"] for line in lines), lines
