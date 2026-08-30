"""Every sound-bar style the picker offers renders properly."""

from __future__ import annotations

from pathlib import Path


def test_legacy_styles_map_to_ones_that_fill_the_box():
    from app.services.scene import parse

    assert parse({"waveStyle": "line"}, 10.0).wave_style == "solid"
    assert parse({"waveStyle": "edge"}, 10.0).wave_style == "solid"
    assert parse({"waveStyle": "bars"}, 10.0).wave_style == "envelope"
    assert parse({"waveStyle": "wideBars"}, 10.0).wave_style == "envelopeChunky"
    assert parse({"waveStyle": "points"}, 10.0).wave_style == "envelopeFine"
    assert parse({"waveStyle": "made-up"}, 10.0).wave_style == "pulse"
    assert parse({"waveStyle": "none"}, 10.0).wave_style == "none"


def test_every_offered_style_has_a_real_renderer():
    from app.services.scene import ENVELOPE_STYLES, PULSE_STYLES

    offered = {"pulse", "pulseFine", "pulseChunky", "envelope", "envelopeFine",
               "envelopeChunky", "solid"}
    assert offered == set(ENVELOPE_STYLES) | set(PULSE_STYLES)


def test_solid_draws_fused_bars(tmp_path):
    from app.services.jobs import build_render_command

    command = build_render_command(
        Path("ep.mp3"), tmp_path / "out.mp4", "9:16", 0.0, 20.0,
        scene={"waveStyle": "solid"}, peaks=[0.2, 0.9, 0.5, 0.7] * 40,
    )
    graph = command[command.index("-filter_complex") + 1]
    assert graph.count("drawbox") >= 100
