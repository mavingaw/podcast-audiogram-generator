"""Word-by-word caption highlighting.

The most recognisable thing about a social clip is the word lighting up as it
is spoken. The word timings were already stored; this is what puts them on the
screen.
"""

from __future__ import annotations

from app.services.jobs import _write_ass
from app.services.scene import BRAND, CAPTION_PRESETS
from app.services.scene import parse as parse_scene
from app.services.transcription import caption_lines


def caption(words: list[tuple[str, float, float]]) -> dict:
    spans = [{"text": t, "start": s, "end": e} for t, s, e in words]
    return {
        "start": spans[0]["start"],
        "end": spans[-1]["end"],
        "text": " ".join(s["text"] for s in spans),
        "words": spans,
    }


LINE = caption([("the", 0.0, 0.4), ("thing", 0.4, 0.9), ("nobody", 0.9, 1.6)])


def events(path) -> list[str]:
    return [
        row for row in path.read_text(encoding="utf-8").splitlines()
        if row.startswith("Dialogue")
    ]


def write(tmp_path, scene: dict, captions=None):
    path = tmp_path / "captions.ass"
    _write_ass(path, captions or [LINE], "9:16", parse_scene(scene, 10.0))
    return path


# --------------------------------------------------------------------------
# Word spans reach the caption lines
# --------------------------------------------------------------------------


def test_caption_lines_carry_their_words():
    tokens = "the thing nobody tells you about this".split()
    words = [
        {"text": w, "start": i * 0.4, "end": i * 0.4 + 0.4}
        for i, w in enumerate(tokens)
    ]
    transcript = {
        "language": "en", "duration": 10.0,
        "segments": [{"id": 1, "speaker": "s", "start": 0.0, "end": 3.0,
                      "text": " ".join(tokens), "words": words}],
    }
    lines = caption_lines(transcript, 0.0, 10.0, max_chars=18)
    assert lines
    for line in lines:
        assert line["words"], line
        # The words rebuild the line exactly.
        assert " ".join(w["text"] for w in line["words"]) == line["text"]


def test_word_timings_are_relative_to_the_clip():
    words = [{"text": "late", "start": 30.0, "end": 30.5}]
    transcript = {
        "language": "en", "duration": 60.0,
        "segments": [{"id": 1, "speaker": "s", "start": 30.0, "end": 30.5,
                      "text": "late", "words": words}],
    }
    line = caption_lines(transcript, 29.0, 40.0)[0]
    assert line["words"][0]["start"] == 1.0


# --------------------------------------------------------------------------
# The events
# --------------------------------------------------------------------------


def test_one_event_per_word(tmp_path):
    rows = events(write(tmp_path, {"captionPreset": "kinder"}))
    # A plated preset adds one carrier event that draws the plate with
    # invisible text; the word events are still one per word.
    words = [row for row in rows if "{\\1a&HFF&}" not in row]
    assert len(words) == 3
    assert len(rows) == 4


def test_each_event_highlights_exactly_one_word(tmp_path):
    for index, row in enumerate(events(write(tmp_path, {"captionPreset": "social"}))):
        assert row.count("{\c&H") == 1, row
        # And it is the word at this position.
        word = LINE["words"][index]["text"]
        assert "}" + word + "{" in row, row


def test_words_hold_until_the_next_one_starts(tmp_path):
    """A gap between words would blink the caption out mid-sentence."""
    rows = events(write(tmp_path, {"captionPreset": "social"}))
    ends = [row.split(",")[2] for row in rows]
    starts = [row.split(",")[1] for row in rows]
    assert ends[:-1] == starts[1:]


def test_the_last_word_holds_to_the_end_of_the_line(tmp_path):
    rows = events(write(tmp_path, {"captionPreset": "social"}))
    assert rows[-1].split(",")[2] == "0:00:01.60"


def test_the_highlight_uses_the_presets_colour(tmp_path):
    from app.services.scene import ass_color

    rows = events(write(tmp_path, {"captionPreset": "social"}))
    assert ass_color(CAPTION_PRESETS["social"]["highlight"]) in rows[0]


def test_every_preset_has_a_highlight_colour():
    for name, preset in CAPTION_PRESETS.items():
        assert preset.get("highlight"), name


def test_highlighting_can_be_turned_off(tmp_path):
    rows = events(write(tmp_path, {"captionPreset": "kinder", "wordHighlight": False}))
    assert len(rows) == 1
    assert "{\c" not in rows[0]


def test_uppercase_presets_still_uppercase_every_word(tmp_path):
    rows = events(write(tmp_path, {"captionPreset": "shout"}))
    assert "THE" in rows[0] and "THING" in rows[0]
    assert "the " not in rows[0]


def test_a_single_word_line_is_not_split(tmp_path):
    """Nothing to highlight against, so it stays one event."""
    one = caption([("hello", 0.0, 0.8)])
    assert len(events(write(tmp_path, {"captionPreset": "social"}, [one]))) == 1


def test_a_line_without_word_timings_falls_back(tmp_path):
    plain = {"start": 0.0, "end": 2.0, "text": "no timings here"}
    rows = events(write(tmp_path, {"captionPreset": "social"}, [plain]))
    assert len(rows) == 1
    assert "no timings here" in rows[0]


def test_zero_length_words_are_skipped(tmp_path):
    """Whisper occasionally emits a word with no duration."""
    odd = caption([("a", 0.0, 0.0), ("real", 0.0, 0.6)])
    rows = events(write(tmp_path, {"captionPreset": "social"}, [odd]))
    assert rows
    for row in rows:
        start, end = row.split(",")[1], row.split(",")[2]
        assert start != end


def test_the_plate_preset_highlights_against_its_plate(tmp_path):
    """Kinder is obsidian type on baby blue, so the lit word must not be blue."""
    assert CAPTION_PRESETS["kinder"]["highlight"] != BRAND["blue"]



def test_a_frosted_plate_is_translucent(tmp_path):
    """The plate alpha is what makes frost glass rather than a white box."""
    from app.services.jobs import _write_ass
    from app.services.scene import parse as parse_scene

    path = tmp_path / "c.ass"
    _write_ass(path, [{"start": 0.0, "end": 1.0, "text": "hi"}], "9:16",
               parse_scene({"captionPreset": "frost"}, 10.0))
    style = next(line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("Style:"))
    fields = style.split(",")
    outline_colour, back_colour = fields[5], fields[6]
    assert outline_colour.startswith("&H55") and back_colour.startswith("&H55"), style
    assert fields[15] == "3", "a plate needs BorderStyle 3"


def test_every_preset_the_picker_offers_renders_a_style(tmp_path):
    from app.services.jobs import _write_ass
    from app.services.scene import CAPTION_PRESETS, parse as parse_scene

    for name in CAPTION_PRESETS:
        path = tmp_path / f"{name}.ass"
        _write_ass(path, [{"start": 0.0, "end": 1.0, "text": "hi"}], "9:16",
                   parse_scene({"captionPreset": name}, 10.0))
        assert "Style: Default," in path.read_text(encoding="utf-8"), name



def test_a_plated_karaoke_line_draws_its_plate_once(tmp_path):
    """libass boxes each text run; a colour override splits the line into runs,
    and a translucent plate showed the seams between them."""
    from app.services.jobs import _write_ass
    from app.services.scene import parse as parse_scene

    path = tmp_path / "c.ass"
    _write_ass(path, [{"start": 0.0, "end": 2.0, "text": "a b", "words": [
        {"start": 0.0, "end": 1.0, "text": "a"}, {"start": 1.0, "end": 2.0, "text": " b"},
    ]}], "9:16", parse_scene({"captionPreset": "frost"}, 10.0))
    events = [l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue:")]
    carriers = [e for e in events if "{\\1a&HFF&}" in e]
    words = [e for e in events if "{\\3a&HFF&\\4a&HFF&}" in e]
    assert len(carriers) == 1, events
    assert len(words) == 2, events
