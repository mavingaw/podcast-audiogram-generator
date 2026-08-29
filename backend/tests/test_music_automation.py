"""Music bed level automation: deliberate dips and swells across the clip."""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from app.services.music_bed import MusicBed, audio_filters, automation_expression, from_scene

ffmpeg_required = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def test_keyframes_are_read_in_order_and_bounded():
    bed = from_scene({"music": {"soundId": "s", "automation": [
        {"at": 20, "gainDb": -40}, {"at": 5, "gainDb": 30}, {"at": -1, "gainDb": 0}, "junk",
    ]}})
    assert bed.automation == ((5.0, 6.0), (20.0, -40.0))


def test_no_keyframes_means_no_expression():
    assert automation_expression((), 30.0) is None
    chains, _ = audio_filters(MusicBed(sound_id="s"), 30.0, has_music_input=True)
    assert "eval=frame" not in ";".join(chains)


def test_the_expression_is_re_evaluated_per_frame():
    expr = automation_expression(((5.0, 0.0), (7.0, -12.0), (10.0, 0.0)), 30.0)
    assert expr.startswith("volume='pow(10")
    assert expr.endswith(":eval=frame")
    # Every comma inside the expression is escaped, or the filter would read
    # them as option separators.
    inner = expr[len("volume='"):expr.rindex("'")]
    assert "," not in inner.replace("\\,", "")


def test_keyframes_past_the_clip_are_ignored():
    expr = automation_expression(((5.0, -6.0), (99.0, 0.0)), 30.0)
    assert "99.000" not in expr


def test_the_ride_sits_after_the_level_and_before_the_fades():
    bed = MusicBed(sound_id="s", automation=((5.0, -12.0),))
    chains, _ = audio_filters(bed, 30.0, has_music_input=True)
    music = next(chain for chain in chains if chain.startswith("[2:a]"))
    assert music.index("volume=-18.00dB") < music.index("eval=frame") < music.index("afade=t=in")


@ffmpeg_required
def test_a_dip_is_audible_as_a_dip(tmp_path):
    """The property: the bed is quieter inside the dip than outside it."""
    tone = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=6", str(tone)], check=True, capture_output=True)
    expr = automation_expression(((2.0, 0.0), (2.5, -20.0), (3.5, -20.0), (4.0, 0.0)), 6.0)
    out = tmp_path / "out.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(tone),
                    "-filter_complex", f"[0:a]{expr}[a]", "-map", "[a]", str(out)], check=True, capture_output=True)

    def level(seconds: float) -> float:
        probe = subprocess.run(["ffmpeg", "-hide_banner", "-ss", str(seconds), "-t", "0.3", "-i", str(out),
                                "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
        return float(re.search(r"max_volume: (-?[0-9.]+) dB", probe.stderr).group(1))

    outside, inside = level(1.0), level(3.0)
    assert inside < outside - 15, f"no dip: {outside:.1f} dB outside, {inside:.1f} dB inside"
