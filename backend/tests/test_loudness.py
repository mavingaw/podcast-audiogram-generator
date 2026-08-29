"""Delivering every clip at a known loudness.

Platforms normalise what you upload, and they normalise *down*. A clip
delivered hot is turned down and lands sounding flat next to one delivered at
the right level; a quiet clip is left quiet. So the level in the file decides
how it sounds in the feed.
"""

from __future__ import annotations

from pathlib import Path

from app.services import loudness
from app.services.jobs import build_render_command

MEASURED = {
    "input_i": "-23.5",
    "input_tp": "-5.2",
    "input_lra": "7.1",
    "input_thresh": "-33.8",
    "target_offset": "0.4",
}


def graph(command: list[str]) -> str:
    return command[command.index("-filter_complex") + 1]


# --------------------------------------------------------------------------
# Parsing FFmpeg's measurement
# --------------------------------------------------------------------------


def test_the_measurement_is_read_out_of_ffmpeg_noise():
    stderr = (
        "ffmpeg version 6.1\n"
        "[Parsed_loudnorm_0 @ 0x1] \n"
        '{\n"input_i" : "-23.50",\n"input_tp" : "-5.20",\n"input_lra" : "7.10",\n'
        '"input_thresh" : "-33.80",\n"target_offset" : "0.40"\n}\n'
    )
    measured = loudness.parse_measurement(stderr)
    assert measured is not None
    assert measured["input_i"] == "-23.50"


def test_the_last_json_block_wins():
    """Some builds print an earlier block; the summary is the final one."""
    stderr = (
        '{"input_i":"-1.0","input_tp":"-1.0","input_lra":"1.0",'
        '"input_thresh":"-1.0","target_offset":"0.0"}\n'
        '{"input_i":"-9.9","input_tp":"-2.0","input_lra":"5.0",'
        '"input_thresh":"-20.0","target_offset":"0.1"}\n'
    )
    assert loudness.parse_measurement(stderr)["input_i"] == "-9.9"


def test_silence_measures_as_infinite_and_is_refused():
    """A silent clip cannot drive a second pass; -inf is not a gain."""
    stderr = (
        '{"input_i":"-inf","input_tp":"-inf","input_lra":"0.00",'
        '"input_thresh":"-inf","target_offset":"0.00"}'
    )
    assert loudness.parse_measurement(stderr) is None


def test_junk_and_missing_fields_are_refused():
    assert loudness.parse_measurement("no json at all") is None
    assert loudness.parse_measurement("") is None
    assert loudness.parse_measurement('{"input_i":"-9.9"}') is None
    assert loudness.parse_measurement('{"input_i": nonsense}') is None


# --------------------------------------------------------------------------
# The filters
# --------------------------------------------------------------------------


def test_the_target_is_the_level_platforms_normalise_to():
    assert loudness.SOCIAL_LUFS == -14.0
    # Headroom for the lossy re-encode every platform performs.
    assert loudness.TRUE_PEAK <= -1.0


def test_the_correcting_pass_uses_a_linear_gain():
    """Dynamic mode pumps on speech, which is the whole material here."""
    assert "linear=true" in loudness.apply_filter(MEASURED)


def test_the_correcting_pass_carries_every_measured_value():
    applied = loudness.apply_filter(MEASURED)
    for value in MEASURED.values():
        assert value in applied


# --------------------------------------------------------------------------
# Where it lands in the render
# --------------------------------------------------------------------------


def test_normalisation_is_applied_to_the_finished_mix():
    command = build_render_command(
        Path("a.wav"), Path("o.mp4"), "9:16", 0.0, 5.0,
        loudness_measurement=MEASURED,
    )
    assert "[anorm]" in graph(command)
    # And it is the label that gets encoded.
    maps = [command[i + 1] for i, arg in enumerate(command) if arg == "-map"]
    assert maps == ["[v]", "[anorm]"]


def test_a_render_without_a_measurement_is_untouched():
    """A failed measurement must not fail or alter the render."""
    command = build_render_command(Path("a.wav"), Path("o.mp4"), "9:16", 0.0, 5.0)
    assert "loudnorm" not in graph(command)
    maps = [command[i + 1] for i, arg in enumerate(command) if arg == "-map"]
    assert maps[1] != "[anorm]"


def test_the_waveforms_own_normalisation_is_not_the_exports():
    """`dynaudnorm` exists to make the drawn waveform fill its box.

    It is on a split branch that never reaches the encoder, and confusing the
    two would mean shipping compressed audio nobody asked for.
    """
    command = build_render_command(
        Path("a.wav"), Path("o.mp4"), "9:16", 0.0, 5.0,
        scene={"waveStyle": "bars",
               "layers": [{"id": "w", "type": "waveform",
                           "x": 10, "y": 71, "width": 80, "height": 9}]},
        loudness_measurement=MEASURED,
    )
    chain = graph(command)
    assert "dynaudnorm" in chain
    # The dynaudnorm branch feeds showwaves, not the audio map.
    wave_branch = next(part for part in chain.split(";") if "dynaudnorm" in part)
    assert "showwaves" in wave_branch
    assert "[anorm]" not in wave_branch


# --------------------------------------------------------------------------
# Actually running it
# --------------------------------------------------------------------------
#
# The tests above assert on the filter string, and every one of them passed
# while this feature was a complete no-op: the measuring pass left the
# waveform's branch of the `asplit` unconnected, FFmpeg refused the graph, and
# the fallback silently shipped un-normalised audio. A graph is only valid if
# FFmpeg accepts it, so these run it.


import shutil
import subprocess

import pytest

ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is not installed"
)


@pytest.fixture
def tone(tmp_path):
    """A quiet 3-second tone: something with a measurable, wrong level."""
    path = tmp_path / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3:sample_rate=48000",
         "-af", "volume=-30dB", "-ac", "2", str(path)],
        check=True, capture_output=True,
    )
    return path


@ffmpeg_required
def test_the_measuring_pass_produces_a_usable_measurement(tone):
    from app.services.jobs import measure_loudness

    measured = measure_loudness(source_path=tone, clip_start=0.0, duration=3.0)
    assert measured is not None, "ffmpeg rejected the measuring graph"
    assert float(measured["input_i"]) < 0


@ffmpeg_required
def test_the_measuring_pass_works_with_a_music_bed(tmp_path, tone):
    """The bed adds a third split output; it must be connected too."""
    from app.services.jobs import measure_loudness
    from app.services.music_bed import MusicBed

    music = tmp_path / "bed.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=220:duration=5:sample_rate=48000",
         "-ac", "2", str(music)],
        check=True, capture_output=True,
    )
    measured = measure_loudness(
        source_path=tone, clip_start=0.0, duration=3.0,
        bed=MusicBed(sound_id="x"), music_path=music,
    )
    assert measured is not None, "ffmpeg rejected the graph with a music bed"


@ffmpeg_required
def test_a_normalised_render_lands_near_the_target(tmp_path, tone):
    """End to end: measure, apply, then measure the result.

    A -30dB tone is far enough from the target that a no-op is unmistakable.
    """
    from app.services.jobs import build_render_command, measure_loudness

    measured = measure_loudness(source_path=tone, clip_start=0.0, duration=3.0)
    assert measured is not None

    # The renderer always burns a subtitle track, and resolves it relative to
    # its working directory.
    from app.services.encoders import CPU
    from app.services.jobs import _write_ass

    _write_ass(tmp_path / "captions.ass", [{"start": 0, "end": 2, "text": "hi"}], "9:16")

    output = tmp_path / "out.mp4"
    subprocess.run(
        # CPU encoding: this asserts on audio, and a test must not depend on
        # the machine running it having an NVIDIA card.
        build_render_command(
            tone, output, "9:16", 0.0, 3.0,
            loudness_measurement=measured, encoder=CPU,
        ),
        check=True, capture_output=True, cwd=tmp_path,
    )
    assert output.exists()

    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(output),
         "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
        capture_output=True, text=True, errors="replace",
    )
    line = next(
        row for row in result.stderr.splitlines()
        if row.strip().startswith("I:") and "LUFS" in row
    )
    loudness_lufs = float(line.split()[1])
    # AAC encoding and a short tone leave a little slack, but nothing like the
    # 18 LU that a silent fallback would show.
    assert abs(loudness_lufs - -14.0) < 2.0, f"exported at {loudness_lufs} LUFS"
