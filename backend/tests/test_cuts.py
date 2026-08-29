"""Striking words out of the transcript takes them out of the audio.

The maths is checked directly, and then the whole thing is checked against
FFmpeg: a file with a silent hole in the middle, the hole cut out, and the
result inspected for silence. A mapping that is a few milliseconds wrong looks
correct in every unit test and sounds wrong in every clip.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import cuts

ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is not installed"
)


def spans(*pairs) -> list[cuts.Span]:
    return [cuts.Span(start, end) for start, end in pairs]


def pairs(items) -> list[tuple[float, float]]:
    return [(span.start, span.end) for span in items]


# --------------------------------------------------------------------------
# Reading what the browser sent
# --------------------------------------------------------------------------


def test_overlapping_cuts_become_one():
    """Two edits over the same words are one hole, not two."""
    parsed = cuts.parse([{"start": 3, "end": 5}, {"start": 4, "end": 6}], 0, 10)
    assert pairs(parsed) == [(3.0, 6.0)]


def test_touching_cuts_are_joined():
    parsed = cuts.parse([{"start": 1, "end": 2}, {"start": 2.005, "end": 3}], 0, 10)
    assert pairs(parsed) == [(1.0, 3.0)]


def test_cuts_are_sorted():
    parsed = cuts.parse([{"start": 7, "end": 8}, {"start": 1, "end": 2}], 0, 10)
    assert pairs(parsed) == [(1.0, 2.0), (7.0, 8.0)]


def test_a_backwards_range_is_read_as_a_range():
    assert pairs(cuts.parse([{"start": 5, "end": 3}], 0, 10)) == [(3.0, 5.0)]


def test_cuts_are_clipped_to_the_clip():
    """The clip edges may have moved since the cut was made."""
    assert pairs(cuts.parse([{"start": 0, "end": 20}], 4, 9)) == [(4.0, 9.0)]
    assert cuts.parse([{"start": 30, "end": 40}], 4, 9) == []


def test_junk_is_ignored_rather_than_raising():
    assert cuts.parse(None, 0, 10) == []
    assert cuts.parse("everything", 0, 10) == []
    assert cuts.parse([{"start": "x", "end": 2}, {}, 5, [1, 2]], 0, 10) == [
        cuts.Span(1.0, 2.0)
    ]


def test_a_zero_length_cut_is_not_a_cut():
    assert cuts.parse([{"start": 3, "end": 3}], 0, 10) == []


# --------------------------------------------------------------------------
# What survives
# --------------------------------------------------------------------------


def test_the_complement_is_what_is_kept():
    keep = cuts.kept(spans((1, 2), (3, 6)), 0, 10)
    assert pairs(keep) == [(0, 1.0), (2.0, 3.0), (6.0, 10)]
    assert cuts.duration(keep) == 6.0


def test_a_cut_at_the_start_leaves_no_empty_span():
    keep = cuts.kept(spans((0, 2)), 0, 10)
    assert pairs(keep) == [(2.0, 10)]


def test_a_cut_at_the_end_leaves_no_empty_span():
    keep = cuts.kept(spans((8, 10)), 0, 10)
    assert pairs(keep) == [(0, 8.0)]


def test_cutting_everything_leaves_nothing():
    assert cuts.kept(spans((0, 10)), 0, 10) == []


# --------------------------------------------------------------------------
# Source time to output time
# --------------------------------------------------------------------------


def test_time_before_any_cut_does_not_move():
    keep = cuts.kept(spans((4, 6)), 0, 10)
    assert cuts.map_time(2.0, keep) == 2.0


def test_time_after_a_cut_moves_up_by_what_was_removed():
    keep = cuts.kept(spans((4, 6)), 0, 10)
    assert cuts.map_time(7.0, keep) == 5.0


def test_time_inside_a_cut_has_no_output_time():
    keep = cuts.kept(spans((4, 6)), 0, 10)
    assert cuts.map_time(5.0, keep) is None


def test_a_cut_moment_clamps_to_the_join():
    """For edges. A caption that runs out of a cut appears at the join."""
    keep = cuts.kept(spans((4, 6)), 0, 10)
    assert cuts.map_clamped(5.0, keep) == 4.0


def test_mapping_is_monotonic_across_several_cuts():
    keep = cuts.kept(spans((2, 3), (5, 7), (8, 8.5)), 0, 10)
    times = [cuts.map_clamped(t / 10, keep) for t in range(0, 100)]
    assert times == sorted(times)
    assert times[-1] <= cuts.duration(keep)


# --------------------------------------------------------------------------
# The transcript
# --------------------------------------------------------------------------


def transcript_of(*words) -> dict:
    return {
        "segments": [{
            "id": 1, "speaker": "Speaker 1",
            "start": words[0][0], "end": words[-1][1],
            "text": " ".join(word[2].strip() for word in words),
            "words": [
                {"start": start, "end": end, "text": text}
                for start, end, text in words
            ],
        }]
    }


def test_a_cut_word_leaves_the_transcript():
    moved = cuts.remap_transcript(
        transcript_of((0, 0.9, "one"), (1.2, 1.8, " two"), (2.4, 3.0, " three")),
        cuts.kept(spans((1, 2)), 0, 3),
    )
    texts = [word["text"] for word in moved["segments"][0]["words"]]
    assert texts == ["one", " three"]


def test_the_segment_text_is_rebuilt_from_what_is_left():
    """Otherwise the caption says a word that is no longer in the audio."""
    moved = cuts.remap_transcript(
        transcript_of((0, 0.9, "one"), (1.2, 1.8, " two"), (2.4, 3.0, " three")),
        cuts.kept(spans((1, 2)), 0, 3),
    )
    assert "two" not in moved["segments"][0]["text"]
    assert moved["segments"][0]["text"] == "one three"


def test_surviving_words_move_onto_the_output_timeline():
    moved = cuts.remap_transcript(
        transcript_of((0, 0.9, "one"), (1.2, 1.8, " two"), (2.4, 3.0, " three")),
        cuts.kept(spans((1, 2)), 0, 3),
    )
    third = moved["segments"][0]["words"][1]
    assert third["start"] == pytest.approx(1.4, abs=0.01)


def test_word_timings_stay_ordered_and_non_zero():
    """A word straddling a join can come out backwards; captions cannot use it."""
    moved = cuts.remap_transcript(
        transcript_of((0, 1.0, "a"), (1.0, 2.0, " b"), (2.0, 3.0, " c")),
        cuts.kept(spans((0.95, 2.05)), 0, 3),
    )
    words = moved["segments"][0]["words"]
    for index, word in enumerate(words):
        assert word["end"] > word["start"]
        if index:
            assert word["start"] >= words[index - 1]["end"]


def test_a_segment_cut_entirely_disappears():
    moved = cuts.remap_transcript(
        transcript_of((5, 6, "gone")), cuts.kept(spans((4, 7)), 0, 10)
    )
    assert moved["segments"] == []


def test_segments_are_renumbered():
    transcript = {"segments": [
        {"id": 1, "start": 0, "end": 1, "text": "a", "words": [{"start": 0, "end": 1, "text": "a"}]},
        {"id": 2, "start": 4, "end": 5, "text": "b", "words": [{"start": 4, "end": 5, "text": "b"}]},
        {"id": 3, "start": 8, "end": 9, "text": "c", "words": [{"start": 8, "end": 9, "text": "c"}]},
    ]}
    moved = cuts.remap_transcript(transcript, cuts.kept(spans((3, 6)), 0, 10))
    assert [segment["id"] for segment in moved["segments"]] == [1, 2]


def test_a_segment_without_word_timings_moves_whole():
    transcript = {"segments": [{"id": 1, "start": 6.0, "end": 8.0, "text": "kept"}]}
    moved = cuts.remap_transcript(transcript, cuts.kept(spans((1, 3)), 0, 10))
    assert moved["segments"][0]["start"] == pytest.approx(4.0)


def test_the_transcript_duration_is_the_new_duration():
    moved = cuts.remap_transcript(
        transcript_of((0, 1, "a")), cuts.kept(spans((4, 6)), 0, 10)
    )
    assert moved["duration"] == 8.0


# --------------------------------------------------------------------------
# Against FFmpeg
# --------------------------------------------------------------------------


def build_holed_file(target: Path) -> None:
    """Six seconds of tone with two seconds of silence in the middle."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-filter_complex",
         "[1]atrim=duration=2,asetpts=PTS-STARTPTS[gap];"
         "[0][gap][2]concat=n=3:v=0:a=1[out]",
         "-map", "[out]", "-ar", "44100", "-ac", "1", str(target)],
        check=True, capture_output=True,
    )


def seconds_of(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def silence_in(path: Path) -> list[str]:
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-af", "silencedetect=noise=-45dB:d=0.4", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return re.findall(r"silence_duration: ([0-9.]+)", out.stderr)


@ffmpeg_required
def test_the_cut_file_is_shorter_by_exactly_what_was_removed(tmp_path):
    source = tmp_path / "holed.wav"
    build_holed_file(source)
    assert seconds_of(source) == pytest.approx(6.0, abs=0.05)

    target = tmp_path / "cut.wav"
    cuts.extract(source, target, cuts.kept(spans((2, 4)), 0, 6))
    assert seconds_of(target) == pytest.approx(4.0, abs=0.05)


@ffmpeg_required
def test_cutting_the_silence_out_leaves_no_silence(tmp_path):
    """The point of the feature, checked by listening rather than by arithmetic."""
    source = tmp_path / "holed.wav"
    build_holed_file(source)
    assert silence_in(source), "the test file was supposed to have a silent hole"

    target = tmp_path / "cut.wav"
    cuts.extract(source, target, cuts.kept(spans((2, 4)), 0, 6))
    assert silence_in(target) == [], "the removed silence is still audible"


@ffmpeg_required
def test_several_cuts_in_one_pass(tmp_path):
    source = tmp_path / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
         "-ar", "44100", "-ac", "1", str(source)],
        check=True, capture_output=True,
    )
    target = tmp_path / "cut.wav"
    cuts.extract(source, target, cuts.kept(spans((1, 2), (4, 5), (7, 9)), 0, 10))
    assert seconds_of(target) == pytest.approx(6.0, abs=0.05)


def test_cutting_everything_is_refused_rather_than_producing_nothing(tmp_path):
    with pytest.raises(RuntimeError, match="cut out"):
        cuts.extract(tmp_path / "x.wav", tmp_path / "y.wav", [])
