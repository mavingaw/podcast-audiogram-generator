"""Ducking driven by the transcript, so the dip is exactly the number set."""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from app.services.music_bed import (
    DUCK_ATTACK, DUCK_RELEASE, MusicBed, audio_filters, automation_expression, duck_keyframes,
)

ffmpeg_required = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def test_the_bed_is_down_by_the_first_word_and_up_after_the_last():
    points = duck_keyframes([(5.0, 8.0)], -12.0, 30.0)
    assert points[0] == (round(5.0 - DUCK_ATTACK, 3), 0.0)
    assert points[1] == (5.0, -12.0)
    assert points[2] == (8.0, -12.0)
    assert points[3] == (round(8.0 + DUCK_RELEASE, 3), 0.0)


def test_a_breath_between_sentences_does_not_surface_the_bed():
    """Words 0.3 s apart are one stretch of speech, not two dips."""
    points = duck_keyframes([(1.0, 2.0), (2.3, 3.0)], -12.0, 30.0)
    assert sum(1 for _, gain in points if gain == 0.0) == 2, points


def test_a_real_pause_lets_the_bed_back_up():
    points = duck_keyframes([(1.0, 2.0), (6.0, 7.0)], -12.0, 30.0)
    assert sum(1 for _, gain in points if gain == 0.0) == 4


def test_no_speech_means_no_keyframes():
    assert duck_keyframes([], -12.0, 30.0) == ()


def test_with_speech_spans_the_compressor_is_replaced_by_the_exact_ride():
    bed = MusicBed(sound_id="s", duck_db=-12.0)
    chains, _ = audio_filters(bed, 30.0, has_music_input=True, speech_spans=[(5.0, 8.0)])
    joined = ";".join(chains)
    assert "sidechaincompress" not in joined
    assert "eval=frame" in joined
    assert "[duckkey]anullsink" in joined


def test_without_a_transcript_the_compressor_still_ducks():
    bed = MusicBed(sound_id="s", duck_db=-12.0)
    chains, _ = audio_filters(bed, 30.0, has_music_input=True, speech_spans=None)
    assert "sidechaincompress" in ";".join(chains)


def test_ducking_off_means_no_ride_even_with_speech():
    bed = MusicBed(sound_id="s", duck_db=0.0)
    chains, _ = audio_filters(bed, 30.0, has_music_input=True, speech_spans=[(5.0, 8.0)])
    assert "eval=frame" not in ";".join(chains)


@ffmpeg_required
def test_the_dip_is_the_number_on_the_slider(tmp_path):
    """The point of doing it this way: -12 means 12, not 'about 8.5'."""
    tone = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=8", str(tone)], check=True, capture_output=True)
    expr = automation_expression(duck_keyframes([(3.0, 5.0)], -12.0, 8.0), 8.0)
    out = tmp_path / "out.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(tone),
                    "-filter_complex", f"[0:a]{expr}[a]", "-map", "[a]", str(out)], check=True, capture_output=True)

    def level(seconds: float) -> float:
        probe = subprocess.run(["ffmpeg", "-hide_banner", "-ss", str(seconds), "-t", "0.3", "-i", str(out),
                                "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
        return float(re.search(r"max_volume: (-?[0-9.]+) dB", probe.stderr).group(1))

    outside, inside = level(1.0), level(4.0)
    assert inside == pytest.approx(outside - 12.0, abs=0.7), f"{outside:.1f} -> {inside:.1f}"


def test_speech_spans_come_from_word_timings_clip_relative():
    from app.services.jobs import speech_spans_of

    transcript = {"segments": [
        {"start": 100, "end": 104, "text": "a b", "words": [
            {"start": 100.5, "end": 101.0, "text": "a"}, {"start": 103.0, "end": 103.5, "text": "b"},
        ]},
        {"start": 110, "end": 112, "text": "later"},
    ]}
    spans = speech_spans_of(transcript, 100.0, 105.0)
    assert spans == [(0.5, 1.0), (3.0, 3.5)]



def test_a_chatty_clip_does_not_produce_an_unbounded_expression():
    """Hundreds of words with tiny gaps must not become hundreds of ifs."""
    words = [(i * 1.0, i * 1.0 + 0.3) for i in range(300)]
    points = duck_keyframes(words, -12.0, 300.0)
    assert len(points) <= 60 * 4
