"""A video source's own footage as the background."""

from __future__ import annotations

from pathlib import Path


# Imported inside the tests, not here: a module-level app import runs at
# collection time, before the fixtures reload the config, and leaves two
# copies of the models fighting over one SQLAlchemy registry.
def graph_of(command: list[str]) -> str:
    return command[command.index("-filter_complex") + 1]


def test_video_sources_show_their_own_footage(tmp_path):
    from app.services.jobs import build_render_command

    command = build_render_command(
        Path("ep.mp4"), tmp_path / "out.mp4", "9:16", 10.0, 20.0,
        scene={"background": "#111111"}, source_has_video=True,
    )
    graph = graph_of(command)
    assert "[0:v]scale=1080:1920" in graph and "crop=1080:1920" in graph
    assert "[vsrc]" in graph


def test_the_footage_can_be_turned_off(tmp_path):
    from app.services.jobs import build_render_command

    command = build_render_command(
        Path("ep.mp4"), tmp_path / "out.mp4", "9:16", 10.0, 20.0,
        scene={"videoBackground": False}, source_has_video=True,
    )
    assert "[0:v]scale" not in graph_of(command)


def test_audio_sources_are_untouched(tmp_path):
    from app.services.jobs import build_render_command

    command = build_render_command(
        Path("ep.mp3"), tmp_path / "out.mp4", "9:16", 10.0, 20.0,
        scene={}, source_has_video=False,
    )
    assert "[0:v]scale" not in graph_of(command)


def test_the_flag_parses_with_footage_on_by_default():
    from app.services.scene import parse

    assert parse({}, 10.0).video_background is True
    assert parse({"videoBackground": False}, 10.0).video_background is False
