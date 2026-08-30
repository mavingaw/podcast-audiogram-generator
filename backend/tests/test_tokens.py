"""Text that fills itself in from the episode.

A template is only reusable if the words on it change with the clip. Without
this, a feed cutting forty clips produces forty videos all reading "Episode
title here", because a template is applied without anybody opening the clips
one at a time — which is the entire point of a feed.

The failure mode worth guarding is the ugly one: a token with nothing behind it
leaving "Episode: " or a dangling dash burned into the corner of a video. That
is worse than no label at all, so an empty value takes its punctuation with it.
"""

from __future__ import annotations

from app.services import tokens


class Episode:
    title = "It's So Hard to Say Goodbye"
    published = "Mon, 25 Aug 2026 09:00:00 GMT"


class Feed:
    title = "Growing Season"


class Project:
    title = "The bit about winter"


class Media:
    original_name = "gst_ep70.mp3"


def context(**overrides) -> dict:
    base = tokens.context_for(
        project=Project(), media=Media(), episode=Episode(), feed=Feed(),
        transcript={"segments": [{"start": 600, "end": 640, "speaker": "Afiya"}]},
        clip_start=612.0, duration=33.4,
    )
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# What the tokens resolve to
# --------------------------------------------------------------------------


def test_the_episode_and_show_come_from_the_feed():
    values = context()
    assert values["episode"] == "It's So Hard to Say Goodbye"
    assert values["show"] == "Growing Season"


def test_the_date_is_written_the_way_a_person_would():
    assert context()["date"] == "25 August 2026"


def test_an_unparseable_date_is_passed_through():
    """A feed's own rendering beats nothing; this is a caption, not a database."""

    class Odd:
        title = "x"
        published = "sometime last spring"

    assert tokens.context_for(episode=Odd())["date"] == "sometime last spring"


def test_a_missing_date_is_empty_rather_than_an_error():
    class NoDate:
        title = "x"
        published = None

    assert tokens.context_for(episode=NoDate())["date"] == ""


def test_the_speaker_is_whoever_is_talking_when_the_clip_starts():
    assert context()["speaker"] == "Afiya"


def test_the_timecode_is_where_the_clip_starts_in_the_episode():
    assert context()["timecode"] == "10:12"


def test_the_duration_is_rounded_to_something_readable():
    assert context()["duration"] == "33"


def test_an_upload_has_no_feed_and_says_so_with_silence():
    values = tokens.context_for(project=Project(), media=Media())
    # No show to name — but the episode is still called something: the
    # project's name stands in, so the title layer is never left blank.
    assert values["show"] == ""
    assert values["episode"] == "The bit about winter"
    assert values["title"] == "The bit about winter"


# --------------------------------------------------------------------------
# Substitution
# --------------------------------------------------------------------------


def test_a_token_becomes_its_value():
    assert tokens.resolve("{{show}}", context()) == "Growing Season"


def test_spacing_inside_the_braces_is_allowed():
    assert tokens.resolve("{{ show }}", context()) == "Growing Season"


def test_case_does_not_matter():
    assert tokens.resolve("{{Show}}", context()) == "Growing Season"


def test_several_tokens_in_one_line():
    assert (
        tokens.resolve("{{show}} — {{episode}}", context())
        == "Growing Season — It's So Hard to Say Goodbye"
    )


def test_text_without_tokens_is_returned_unchanged():
    assert tokens.resolve("Just a label", context()) == "Just a label"


def test_an_unknown_token_is_left_alone():
    """Someone typing literal braces should see what they typed."""
    assert "{{not_a_token}}" in tokens.resolve("{{not_a_token}}", context())


# --------------------------------------------------------------------------
# Empty values, which is where this gets ugly
# --------------------------------------------------------------------------


def test_an_empty_value_does_not_leave_a_dangling_separator():
    """'Episode: ' burned into a video is worse than nothing there."""
    assert tokens.resolve("Episode: {{episode}}", context(episode="")) == "Episode"


def test_an_empty_value_does_not_leave_a_dangling_dash():
    assert tokens.resolve("{{show}} — {{episode}}", context(episode="")) == "Growing Season"


def test_a_gap_left_in_the_middle_is_closed():
    filled = tokens.resolve("{{show}} {{episode}} live", context(episode=""))
    assert "  " not in filled
    assert filled == "Growing Season live"


def test_a_layer_that_is_only_an_empty_token_comes_out_empty():
    assert tokens.resolve("{{episode}}", context(episode="")).strip() == ""


# --------------------------------------------------------------------------
# Discoverability
# --------------------------------------------------------------------------


def test_every_token_in_the_documented_set_resolves():
    """The editor lists these; a listed token that does nothing is a lie."""
    values = context()
    for name in tokens.TOKENS:
        assert name in values, f"{name} is offered but has no value"


def test_used_in_reports_which_tokens_a_layer_refers_to():
    assert tokens.used_in("{{show}} — {{episode}}") == {"show", "episode"}
    assert tokens.used_in("{{unknown}}") == set()
    assert tokens.used_in("") == set()


# --------------------------------------------------------------------------
# Reaching the render
# --------------------------------------------------------------------------


def test_a_text_layer_is_drawn_with_the_token_resolved(tmp_path):
    from pathlib import Path

    from app.services.jobs import build_render_command

    scene = {"layers": [
        {"id": "t", "type": "text", "x": 10, "y": 80, "width": 80, "height": 8,
         "text": "{{show}}"},
    ]}
    graph = build_render_command(
        Path("a.wav"), tmp_path / "o.mp4", "9:16", 0.0, 10.0, scene=scene,
        token_context={"show": "Growing Season"},
    )
    chain = graph[graph.index("-filter_complex") + 1]
    # The words go in a file beside the render, not into the graph: no inline
    # escaping of an apostrophe works. See _text_filters.
    assert "textfile='text-0.txt'" in chain
    assert (tmp_path / "text-0.txt").read_text(encoding="utf-8") == "Growing Season"


def test_a_text_layer_whose_token_is_empty_is_not_drawn(tmp_path):
    """An invisible box costs a filter pass and draws nothing."""
    from pathlib import Path

    from app.services.jobs import build_render_command

    scene = {"layers": [
        {"id": "t", "type": "text", "x": 10, "y": 80, "width": 80, "height": 8,
         "text": "{{episode}}"},
    ]}
    graph = build_render_command(
        Path("a.wav"), tmp_path / "o.mp4", "9:16", 0.0, 10.0, scene=scene,
        token_context={"episode": ""},
    )
    chain = graph[graph.index("-filter_complex") + 1]
    assert "drawtext" not in chain


def test_plain_text_layers_still_render_without_any_context(tmp_path):
    from pathlib import Path

    from app.services.jobs import build_render_command

    scene = {"layers": [
        {"id": "t", "type": "text", "x": 10, "y": 80, "width": 80, "height": 8,
         "text": "A fixed label"},
    ]}
    graph = build_render_command(
        Path("a.wav"), tmp_path / "o.mp4", "9:16", 0.0, 10.0, scene=scene
    )
    chain = graph[graph.index("-filter_complex") + 1]
    assert "drawtext=" in chain
    assert (tmp_path / "text-0.txt").read_text(encoding="utf-8") == "A fixed label"


# --------------------------------------------------------------------------
# The real render path
# --------------------------------------------------------------------------
#
# The graph-string tests above all passed while the actual render failed with
# `name 'token_context' is not defined`: the parameter had been threaded into
# `build_render_command` but not into the function that calls it. Asserting on
# a string the builder returns cannot see that, so this runs the encode.

import shutil
import subprocess

import pytest

ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is not installed"
)


@ffmpeg_required
def test_a_render_with_tokens_actually_completes(tmp_path):
    from app.services.jobs import _render_audiogram_mp4, _write_ass

    source = tmp_path / "voice.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=220:duration=3",
         "-ar", "44100", "-ac", "1", str(source)],
        check=True, capture_output=True,
    )
    ass_path = tmp_path / "captions.ass"
    _write_ass(ass_path, [{"start": 0.0, "end": 2.0, "text": "hello"}], "9:16", None)

    output = tmp_path / "out.mp4"
    _render_audiogram_mp4(
        source_path=source,
        output_path=output,
        ass_path=ass_path,
        aspect_ratio="9:16",
        clip_start=0.0,
        duration=3.0,
        scene={"layers": [
            {"id": "bg", "type": "background", "x": 0, "y": 0,
             "width": 100, "height": 100},
            {"id": "t", "type": "title", "x": 8, "y": 10, "width": 84,
             "height": 7, "text": "{{show}}", "color": "#ffffff"},
        ]},
        token_context={"show": "Growing Season"},
    )
    assert output.exists() and output.stat().st_size > 1000


@ffmpeg_required
def test_a_title_with_an_apostrophe_renders(tmp_path):
    """The bug that made textfile= necessary.

    Inside a single-quoted filtergraph value FFmpeg does not treat a backslash
    as an escape, so an apostrophe ends the quote and the rest of the graph —
    including the output label — is parsed as garbage. Four inline escapings
    were tried against a real encode and all four failed identically, so any
    text layer containing an apostrophe had never rendered. Real episode titles
    are full of them.
    """
    from app.services.jobs import _render_audiogram_mp4, _write_ass

    source = tmp_path / "voice.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=220:duration=2",
         "-ar", "44100", "-ac", "1", str(source)],
        check=True, capture_output=True,
    )
    ass_path = tmp_path / "captions.ass"
    _write_ass(ass_path, [{"start": 0.0, "end": 1.5, "text": "x"}], "9:16", None)

    output = tmp_path / "out.mp4"
    _render_audiogram_mp4(
        source_path=source, output_path=output, ass_path=ass_path,
        aspect_ratio="9:16", clip_start=0.0, duration=2.0,
        scene={"layers": [
            {"id": "bg", "type": "background", "x": 0, "y": 0,
             "width": 100, "height": 100},
            {"id": "t", "type": "title", "x": 8, "y": 10, "width": 84,
             "height": 7, "text": "{{episode}}", "color": "#ffffff"},
        ]},
        token_context={
            # Every character that has broken this: apostrophe, em dash,
            # colon, percent, and a backslash for completeness.
            "episode": "It's So Hard — Goodbye: 100% \ done",
        },
    )
    assert output.exists() and output.stat().st_size > 1000


def test_an_uploaded_episode_still_has_a_title():
    """No feed, no episode record: {{episode}} falls back to the project's
    name, then the file's. It used to be empty, and an empty title layer is
    skipped — so every clip cut from an upload rendered without a title."""
    from types import SimpleNamespace

    from app.services.tokens import context_for, resolve

    project = SimpleNamespace(title="Season 4, Ep. 69")
    media = SimpleNamespace(original_name="ep69.mp3")
    ctx = context_for(project=project, media=media, episode=None, feed=None,
                      transcript=None, clip_start=0.0, duration=30.0)
    assert resolve("{{episode}}", ctx) == "Season 4, Ep. 69"
    ctx = context_for(project=SimpleNamespace(title=""), media=media, episode=None, feed=None,
                      transcript=None, clip_start=0.0, duration=30.0)
    assert resolve("{{episode}}", ctx) == "ep69"
    feed_ep = SimpleNamespace(title="From the feed", published=None)
    ctx = context_for(project=project, media=media, episode=feed_ep, feed=None,
                      transcript=None, clip_start=0.0, duration=30.0)
    assert resolve("{{episode}}", ctx) == "From the feed"
