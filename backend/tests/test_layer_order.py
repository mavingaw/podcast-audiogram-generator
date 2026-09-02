"""The export composites layers in the order the editor lists them.

"Bring forward" in the panel reordered the preview and left the video alone:
every picture went under the sound bars and every title over the captions,
whatever the stack said. Now the filter graph follows the stack.
"""

from __future__ import annotations

from pathlib import Path

from app.services.jobs import build_render_command
from app.services.plates import Plates


def _graph(layers: list[dict], tmp_path: Path, **scene) -> str:
    command = build_render_command(
        Path("s.mp3"), tmp_path / "o.mp4", "9:16", 0.0, 12.0,
        scene={"layers": layers, **scene}, font_file=Path("/f.ttf"),
        peaks=[0.2, 0.9, 0.4] * 40,
        image_paths={"cover": Path("cover.png")},
        plates=Plates(artwork={"art": Path("art.png")}),
    )
    return command[command.index("-filter_complex") + 1]


ART = {"id": "art", "type": "artwork", "mediaId": "cover", "x": 10, "y": 10, "width": 60, "height": 30}
WAVE = {"id": "wave", "type": "waveform", "x": 10, "y": 50, "width": 80, "height": 12}
TITLE = {"id": "title", "type": "title", "text": "Hi", "x": 10, "y": 5, "width": 80, "height": 8}
CAPTIONS = {"id": "captions", "type": "captions", "x": 10, "y": 70, "width": 80, "height": 15}


def _position(graph: str, marker: str) -> int:
    assert marker in graph, marker
    return graph.index(marker)


def test_the_default_stack_draws_artwork_then_bars_then_title_then_captions(tmp_path):
    graph = _graph([ART, WAVE, TITLE, CAPTIONS], tmp_path, waveStyle="pulse")
    art = _position(graph, "[vimg0]")
    bars = _position(graph, "showfreqs")
    title = _position(graph, "drawtext=")
    captions = _position(graph, "ass=captions.ass")
    assert art < bars < title < captions


def test_artwork_listed_above_the_bars_is_drawn_over_them(tmp_path):
    graph = _graph([WAVE, ART, TITLE, CAPTIONS], tmp_path, waveStyle="pulse")
    # The overlay that places the artwork consumes the sound-bar composite,
    # so the picture lands on top of the bars.
    bars = _position(graph, "showfreqs")
    art = _position(graph, "overlay=x=108")
    assert bars < art


def test_a_title_under_the_artwork_is_covered_by_it(tmp_path):
    graph = _graph([TITLE, ART, WAVE, CAPTIONS], tmp_path, waveStyle="pulse")
    assert _position(graph, "drawtext=") < _position(graph, "[vimg0]")


def test_captions_placed_by_their_layer_sit_under_a_later_title(tmp_path):
    graph = _graph([ART, WAVE, CAPTIONS, TITLE], tmp_path, waveStyle="pulse")
    assert _position(graph, "ass=captions.ass") < _position(graph, "drawtext=")


def test_without_a_captions_layer_captions_still_burn_in_on_top(tmp_path):
    graph = _graph([ART, WAVE, TITLE], tmp_path, waveStyle="pulse")
    assert _position(graph, "drawtext=") < _position(graph, "ass=captions.ass")
    assert graph.rstrip().endswith("[v]")


def test_a_hidden_layer_is_skipped_but_the_order_holds(tmp_path):
    graph = _graph([{**WAVE, "visible": False}, ART, TITLE, CAPTIONS], tmp_path, waveStyle="pulse")
    assert "showfreqs" not in graph
    assert "[wavesrc]anullsink" in graph
    assert _position(graph, "[vimg0]") < _position(graph, "drawtext=")


def test_every_text_layer_gets_its_own_file(tmp_path):
    second = {**TITLE, "id": "t2", "text": "Second", "y": 90}
    _graph([ART, TITLE, WAVE, second, CAPTIONS], tmp_path, waveStyle="pulse")
    assert (tmp_path / "text-0.txt").read_text(encoding="utf-8") == "Hi"
    assert (tmp_path / "text-1.txt").read_text(encoding="utf-8") == "Second"
