"""The customization dials reach the renderer."""

from __future__ import annotations

from pathlib import Path


def test_layer_font_scale_and_align_parse_and_clamp():
    from app.services.scene import parse

    scene = {"layers": [
        {"id": "t", "type": "title", "x": 10, "y": 10, "width": 60, "height": 10,
         "fontScale": 1.3, "align": "left"},
    ]}
    layer = parse(scene, 10.0).layers[0]
    assert layer.font_scale == 1.3
    assert layer.align == "left"

    wild = {"layers": [
        {"id": "t", "type": "title", "x": 10, "y": 10, "width": 60, "height": 10,
         "fontScale": 99, "align": "sideways"},
    ]}
    layer = parse(wild, 10.0).layers[0]
    assert layer.font_scale == 1.6
    assert layer.align == "center"


def test_caption_scale_changes_the_ass_font_size(tmp_path):
    from app.services.jobs import _write_ass
    from app.services.scene import parse

    captions = [{"start": 0.0, "end": 2.0, "text": "hello"}]

    def size_of(scene):
        out = tmp_path / f"{len(list(tmp_path.iterdir()))}.ass"
        _write_ass(out, captions, "9:16", parse(scene, 10.0))
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.startswith("Style: Default,"):
                return int(line.split(",")[2])
        raise AssertionError("no style line")

    assert size_of({"captionScale": 1.5}) > size_of({}) > size_of({"captionScale": 0.6})


def test_align_moves_the_drawtext_expression(tmp_path):
    from app.services.jobs import _text_filters
    from app.services.scene import parse as parse_scene

    def graph(align):
        scene = {"layers": [
            {"id": "t", "type": "title", "text": "Hello There", "x": 10,
             "y": 10, "width": 80, "height": 10, "align": align},
        ]}
        return _text_filters(
            parse_scene(scene, 10.0), 1080, 1920, 10.0,
            tmp_path / "f.ttf", work_dir=tmp_path,
        )

    left, right, centre = graph("left"), graph("right"), graph("center")
    assert "-text_w)/2" in centre
    assert "-text_w)/2" not in left
    assert "-text_w)" in right and "-text_w)/2" not in right
