"""Layer opacity: what makes a logo a watermark."""

from __future__ import annotations

from app.services.scene import parse


def test_opacity_parses_and_clamps():
    scene = {"layers": [
        {"id": "w", "type": "artwork", "opacity": 0.5},
        {"id": "solid", "type": "artwork"},
        {"id": "zero", "type": "artwork", "opacity": 0},
        {"id": "over", "type": "artwork", "opacity": 9},
    ]}
    layers = {l.id: l for l in parse(scene, 10.0).layers}
    assert layers["w"].opacity == 0.5
    assert layers["solid"].opacity == 1.0
    # Fully invisible would be a support call; clamped to faint instead.
    assert layers["zero"].opacity == 0.05
    assert layers["over"].opacity == 1.0


def test_a_translucent_text_layer_reaches_drawtext(tmp_path):
    from app.services.jobs import _text_filters
    from app.services.scene import parse as parse_scene

    scene = {"layers": [{"id": "t", "type": "title", "text": "Hi", "opacity": 0.55,
                         "x": 10, "y": 10, "w": 80, "h": 20}]}
    chain = _text_filters(parse_scene(scene, 10.0), 1080, 1920, 10.0, tmp_path / "f.ttf", work_dir=tmp_path)
    assert "alpha=0.550" in chain
