"""A damaged file must not become a silently short transcript.

An MP3 with one bad frame at 9:04 transcribed as nine minutes of a 58-minute
episode and reported "Transcript ready". Two things keep that from happening
again: the audio goes through ffmpeg first, which skips the frame, and a
transcript that stops well short of the audio says so in the result.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services import preview, transcription


class FakeModel:
    def __init__(self, ends: list[float], duration: float):
        self.ends = ends
        self.duration = duration
        self.seen: list[str] = []

    def transcribe(self, path, **kwargs):
        self.seen.append(path)
        segments = [
            SimpleNamespace(start=max(0.0, e - 2.0), end=e, text=f"words {i}", words=[])
            for i, e in enumerate(self.ends)
        ]
        return iter(segments), SimpleNamespace(duration=self.duration, language="en")


def _run(monkeypatch, tmp_path, ends, duration):
    model = FakeModel(ends, duration)
    runtime = SimpleNamespace(model_size="tiny", device="cpu", device_index=None)
    monkeypatch.setattr(transcription, "choose_runtime", lambda *a, **k: runtime)
    monkeypatch.setattr(transcription, "load_model", lambda r: model)
    # No ffmpeg in the test box: the fallback hands the original file over.
    monkeypatch.setattr(transcription.subprocess if hasattr(transcription, "subprocess") else __import__("subprocess"), "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no ffmpeg")))
    audio = tmp_path / "ep.mp3"
    audio.write_bytes(b"\xff\xfb" * 100)
    return transcription.transcribe(audio), model


def test_a_transcript_that_stops_early_carries_a_warning(monkeypatch, tmp_path):
    result, _ = _run(monkeypatch, tmp_path, ends=[100.0, 300.0, 543.3], duration=3500.0)
    assert result["warnings"], result
    assert "9:03" in result["warnings"][0] and "58:20" in result["warnings"][0]


def test_a_full_transcript_carries_no_warning(monkeypatch, tmp_path):
    result, _ = _run(monkeypatch, tmp_path, ends=[100.0, 3000.0, 3480.0], duration=3500.0)
    assert "warnings" not in result


def test_short_clips_are_never_second_guessed(monkeypatch, tmp_path):
    """A 90-second file that is mostly music is not damaged."""
    result, _ = _run(monkeypatch, tmp_path, ends=[5.0], duration=90.0)
    assert "warnings" not in result


def test_without_ffmpeg_the_original_file_is_transcribed(monkeypatch, tmp_path):
    _, model = _run(monkeypatch, tmp_path, ends=[10.0], duration=20.0)
    assert model.seen and model.seen[0].endswith("ep.mp3")


def test_the_job_message_carries_the_warning():
    from app.services import jobs

    source = Path(jobs.__file__).read_text(encoding="utf-8")
    assert 'transcript.get("warnings")' in source


# ---------------------------------------------------------------- previews


def test_mp3_sources_are_cut_to_mp3(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, "settings", SimpleNamespace(work_dir=tmp_path))
    assert preview.cache_path("m", 0.0, 10.0, Path("ep.mp3")).suffix == ".mp3"
    assert preview.cache_path("m", 0.0, 10.0, Path("ep.MP3")).suffix == ".mp3"
    assert preview.cache_path("m", 0.0, 10.0, Path("ep.wav")).suffix == ".m4a"
    assert preview.cache_path("m", 0.0, 10.0).suffix == ".m4a"


def test_mp3_cuts_copy_the_stream(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"x" * 2048)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(preview.subprocess, "run", fake_run)
    target = tmp_path / "cut.mp3"
    preview._cut(Path("ep.mp3"), 1.0, 5.0, target)
    assert target.exists()
    assert "copy" in calls[0] and "aac" not in calls[0]
    target = tmp_path / "cut.m4a"
    preview._cut(Path("ep.wav"), 1.0, 5.0, target)
    assert "aac" in calls[1]
