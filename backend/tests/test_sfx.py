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



def test_a_cue_can_point_at_a_recording_instead_of_a_library_sound():
    cues = sfx.parse({"sfx": [{"mediaId": "rec-1", "at": 2.0}, {"soundId": "lib", "at": 4.0}]}, 10.0)
    assert cues[0].media_id == "rec-1" and cues[0].sound_id == ""
    assert cues[1].sound_id == "lib" and cues[1].media_id is None


def test_a_voiceover_is_saved_without_the_episode_jobs(monkeypatch, tmp_path):
    from tests.test_api import create_test_client

    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "owner", "password": "Passw0rd!enough"})
    project = client.post("/api/projects", json={"title": "c"}).json()["project"]

    response = client.post(
        f"/api/projects/{project['id']}/voiceover",
        files={"file": ("take.webm", b"\x1aE\xdf\xa3webm-bytes", "audio/webm;codecs=opus")},
    )
    assert response.status_code == 200, response.text
    media = response.json()["media"]
    assert media["original_name"].startswith("Voice-over for")
    # No analysis, waveform or transcription queued for a ten-second aside.
    kinds = {job["kind"] for job in client.get("/api/jobs").json()["jobs"]}
    assert "transcribe" not in kinds

    from app.core.config import settings

    assert any(p.suffix == ".webm" for p in settings.uploads_dir.iterdir())


def test_an_unsupported_recording_format_is_refused(monkeypatch, tmp_path):
    from tests.test_api import create_test_client

    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "owner", "password": "Passw0rd!enough"})
    project = client.post("/api/projects", json={"title": "c"}).json()["project"]
    response = client.post(
        f"/api/projects/{project['id']}/voiceover",
        files={"file": ("take.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 415


@ffmpeg_required
def test_a_browser_recording_in_webm_opus_mixes_in(tmp_path):
    """MediaRecorder hands over webm/opus, not wav; the render has to read it."""
    from app.services.jobs import _render_audiogram_mp4, _write_ass

    silent = tmp_path / "silent.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "anullsrc=r=44100:cl=mono:d=4", str(silent)], check=True, capture_output=True)
    take = tmp_path / "take.webm"
    made = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                           "-i", "sine=frequency=660:duration=0.6", "-c:a", "libopus", str(take)],
                          capture_output=True)
    if made.returncode != 0:
        pytest.skip("this ffmpeg cannot encode opus")
    ass_path = tmp_path / "captions.ass"
    _write_ass(ass_path, [{"start": 0.0, "end": 1.0, "text": "x"}], "9:16", None)
    output = tmp_path / "out.mp4"
    _render_audiogram_mp4(
        source_path=silent, output_path=output, ass_path=ass_path, aspect_ratio="9:16",
        clip_start=0.0, duration=4.0, scene={"layers": []},
        sfx=[sfx.ResolvedCue(take, 1.5, 0.0)],
    )
    probe = subprocess.run(["ffmpeg", "-hide_banner", "-ss", "1.7", "-t", "0.2", "-i", str(output),
                            "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
    level = float(re.search(r"max_volume: (-?[0-9.]+) dB", probe.stderr).group(1))
    assert level > -30, f"the recording is not audible at its time ({level} dB)"
