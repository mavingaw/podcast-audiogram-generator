"""Dragged captions land where they were dropped."""

from __future__ import annotations


def test_caption_y_parses_and_clamps():
    from app.services.scene import parse

    assert parse({}, 10.0).caption_y is None
    assert parse({"captionY": 30}, 10.0).caption_y == 30.0
    assert parse({"captionY": -5}, 10.0).caption_y == 2.0
    assert parse({"captionY": 400}, 10.0).caption_y == 88.0
    assert parse({"captionY": "nonsense"}, 10.0).caption_y is None


def test_dragged_captions_move_the_ass_margin(tmp_path):
    from app.services.jobs import _write_ass
    from app.services.scene import parse

    captions = [{"start": 0.0, "end": 2.0, "text": "hello there"}]

    default = tmp_path / "default.ass"
    _write_ass(default, captions, "9:16", parse({}, 10.0))

    high = tmp_path / "high.ass"
    _write_ass(high, captions, "9:16", parse({"captionY": 20}, 10.0))

    def margin_of(path):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("Style: Default,"):
                return int(line.split(",")[-2])
        raise AssertionError("no style line")

    # Dropped near the top of the frame -> a much larger bottom margin.
    assert margin_of(high) > margin_of(default) + 400


def test_caption_y_none_keeps_the_preset_margin(tmp_path):
    from app.services.jobs import _write_ass
    from app.services.scene import CAPTION_PRESETS, parse

    captions = [{"start": 0.0, "end": 2.0, "text": "hello"}]
    out = tmp_path / "out.ass"
    _write_ass(out, captions, "9:16", parse({}, 10.0))
    preset = CAPTION_PRESETS["social"]
    expected = int(1920 * preset["margin_ratio"])
    for line in out.read_text(encoding="utf-8").splitlines():
        if line.startswith("Style: Default,"):
            assert int(line.split(",")[-2]) == expected
            return
    raise AssertionError("no style line")
