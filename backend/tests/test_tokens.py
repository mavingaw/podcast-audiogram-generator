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
    assert values["episode"] == "" and values["show"] == ""
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
        Path("a.wav"), Path("o.mp4"), "9:16", 0.0, 10.0, scene=scene,
        token_context={"show": "Growing Season"},
    )
    chain = graph[graph.index("-filter_complex") + 1]
    assert "Growing Season" in chain
    assert "{{show}}" not in chain


def test_a_text_layer_whose_token_is_empty_is_not_drawn(tmp_path):
    """An invisible box costs a filter pass and draws nothing."""
    from pathlib import Path

    from app.services.jobs import build_render_command

    scene = {"layers": [
        {"id": "t", "type": "text", "x": 10, "y": 80, "width": 80, "height": 8,
         "text": "{{episode}}"},
    ]}
    graph = build_render_command(
        Path("a.wav"), Path("o.mp4"), "9:16", 0.0, 10.0, scene=scene,
        token_context={"episode": ""},
    )
    chain = graph[graph.index("-filter_complex") + 1]
    assert "drawtext" not in chain


def test_plain_text_layers_still_render_without_any_context():
    from pathlib import Path

    from app.services.jobs import build_render_command

    scene = {"layers": [
        {"id": "t", "type": "text", "x": 10, "y": 80, "width": 80, "height": 8,
         "text": "A fixed label"},
    ]}
    graph = build_render_command(Path("a.wav"), Path("o.mp4"), "9:16", 0.0, 10.0, scene=scene)
    chain = graph[graph.index("-filter_complex") + 1]
    assert "A fixed label" in chain
