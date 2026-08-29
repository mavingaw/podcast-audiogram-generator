"""One-shot sound effects placed at points in a clip."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import sfx

ffmpeg_required = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def test_cues_are_read_in_time_order_and_bounded():
    cues = sfx.parse({"sfx": [
        {"soundId": "b", "at": 8.0, "gainDb": 40},
        {"soundId": "a", "at": 1.5, "gainDb": -3},
        {"soundId": "late", "at": 30.0},
        {"soundId": "neg", "at": -1},
        {"at": 2.0},
        {"soundId": "junk", "at": "soon"},
    ]}, 10.0)
    assert [c.sound_id for c in cues] == ["a", "b"]
    assert cues[1].gain_db == sfx.MAX_GAIN_DB, "gain was not bounded"


def test_no_cues_when_the_scene_has_none():
    assert sfx.parse({}, 10.0) == []
    assert sfx.parse(None, 10.0) == []
    assert sfx.parse({"sfx": "x"}, 10.0) == []


def test_the_cue_count_is_capped():
    cues = sfx.parse({"sfx": [{"soundId": str(i), "at": i * 0.1} for i in range(40)]}, 10.0)
    assert len(cues) == sfx.MAX_CUES


def test_filters_delay_each_cue_and_mix_at_unity():
    chains, label = sfx.filters([
        sfx.ResolvedCue(Path("a.mp3"), 1.25, -6.0),
        sfx.ResolvedCue(Path("b.wav"), 8.0, 0.0),
    ], first_input=4, mix_label="[aout]")
    assert "[4:a]" in chains[0] and "adelay=1250|1250" in chains[0] and "volume=-6.00dB" in chains[0]
    assert "[5:a]" in chains[1] and "adelay=8000|8000" in chains[1]
    assert chains[-1].startswith("[aout][fx0][fx1]amix=inputs=3:normalize=0")
    assert label == "[amixfx]"


def test_no_cues_leaves_the_mix_alone():
    assert sfx.filters([], 4, "[aout]") == ([], "[aout]")


def test_the_render_adds_one_input_per_cue_before_the_loudness_pass():
    from app.services.jobs import build_render_command

    cues = [sfx.ResolvedCue(Path("whoosh.mp3"), 1.0, 0.0)]
    cmd = build_render_command(
        Path("a.wav"), Path("o.mp4"), "9:16", 0.0, 10.0, scene={"layers": []}, sfx=cues,
        loudness_measurement={"input_i": -20, "input_tp": -3, "input_lra": 5, "input_thresh": -30, "target_offset": 0},
    )
    inputs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"]
    assert inputs[-1] == "whoosh.mp3"
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert graph.index("amix") < graph.index("loudnorm")


@ffmpeg_required
def test_a_cue_is_audible_at_its_time_and_not_before(tmp_path):
    """A silent clip with one tone cue at 2 s: there must be sound at 2.2 s
    and none at 0.5 s. This is the property; the graph string is not."""
    from app.services.jobs import _render_audiogram_mp4, _write_ass

    silent = tmp_path / "silent.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "anullsrc=r=44100:cl=mono:d=4", str(silent)], check=True, capture_output=True)
    tone = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "sine=frequency=880:duration=0.5", str(tone)], check=True, capture_output=True)
    ass_path = tmp_path / "captions.ass"
    _write_ass(ass_path, [{"start": 0.0, "end": 1.0, "text": "x"}], "9:16", None)
    output = tmp_path / "out.mp4"
    _render_audiogram_mp4(
        source_path=silent, output_path=output, ass_path=ass_path, aspect_ratio="9:16",
        clip_start=0.0, duration=4.0, scene={"layers": []},
        sfx=[sfx.ResolvedCue(tone, 2.0, 0.0)],
    )

    def level_at(seconds: float) -> float:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-ss", str(seconds), "-t", "0.2", "-i", str(output),
             "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True,
        )
        match = re.search(r"max_volume: (-?[0-9.]+) dB", out.stderr)
        return float(match.group(1)) if match else -91.0

    assert level_at(2.15) > -30, "the cue is not audible at its time"
    assert level_at(0.5) < -60, "there is sound before the cue"
