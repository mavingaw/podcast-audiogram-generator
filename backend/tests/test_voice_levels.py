"""Level and edges on the voice itself.

Loudness normalisation sets the level of the whole file; it cannot help with
what happens in a clip's first quarter-second. A clip cut out of the middle of
an episode starts and ends abruptly, and a short fade is the difference between
a clip that begins and one that is spliced.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.jobs import build_render_command
from app.services.music_bed import MusicBed, voice_shaping
from app.services.scene import parse as parse_scene


def graph(scene: dict | None = None, duration: float = 10.0) -> str:
    command = build_render_command(
        Path("a.wav"), Path("o.mp4"), "9:16", 0.0, duration, scene=scene
    )
    return command[command.index("-filter_complex") + 1]


# --------------------------------------------------------------------------
# The scene
# --------------------------------------------------------------------------


def test_nothing_is_applied_by_default():
    """Silence about levels must mean the audio is untouched."""
    parsed = parse_scene({}, 10.0)
    assert parsed.voice_gain_db == 0.0
    assert parsed.fade_in == 0.0 and parsed.fade_out == 0.0
    chain = graph()
    assert "volume=" not in chain and "afade" not in chain


def test_values_are_read_from_the_scene():
    parsed = parse_scene({"voiceGainDb": -3.5, "fadeIn": 0.4, "fadeOut": 1.2}, 10.0)
    assert parsed.voice_gain_db == -3.5
    assert (parsed.fade_in, parsed.fade_out) == (0.4, 1.2)


def test_absurd_values_are_bounded():
    """A stored scene is not a promise; +40dB is a broken render."""
    parsed = parse_scene({"voiceGainDb": 40, "fadeIn": -3, "fadeOut": 900}, 10.0)
    assert -24.0 <= parsed.voice_gain_db <= 12.0
    assert parsed.fade_in >= 0.0
    assert parsed.fade_out <= 10.0


def test_nonsense_values_fall_back_rather_than_failing():
    parsed = parse_scene({"voiceGainDb": "loud", "fadeIn": None}, 10.0)
    assert parsed.voice_gain_db == 0.0
    assert parsed.fade_in == 0.0


# --------------------------------------------------------------------------
# The filters
# --------------------------------------------------------------------------


def test_gain_reaches_the_render():
    assert "volume=-3.50dB" in graph({"voiceGainDb": -3.5})


def test_fades_reach_the_render():
    chain = graph({"fadeIn": 0.4, "fadeOut": 1.2}, duration=10.0)
    assert "afade=t=in:st=0:d=0.400" in chain
    # The fade out has to end with the clip, not start at its end.
    assert "afade=t=out:st=8.800:d=1.200" in chain


def test_a_fade_longer_than_the_clip_is_clamped():
    """Otherwise a two-second clip rises out of silence for its whole length."""
    chain = graph({"fadeIn": 9.0}, duration=2.0)
    seconds = float(re.search(r"afade=t=in:st=0:d=([0-9.]+)", chain).group(1))
    assert seconds <= 1.0


def test_shaping_is_empty_when_nothing_is_asked_for():
    assert voice_shaping(0.0, 0.0, 0.0, 10.0) == ""


def test_shaping_starts_with_a_separator_so_it_can_be_appended():
    assert voice_shaping(-3.0, 0.0, 0.0, 10.0).startswith(",")


# --------------------------------------------------------------------------
# The parts that must stay in step
# --------------------------------------------------------------------------


def test_the_drawn_waveform_is_shaped_like_the_audio():
    """Fading the sound but not the picture looks like a bug, not a fade."""
    chain = graph({
        "fadeIn": 0.5,
        "waveStyle": "bars",
        "layers": [{"id": "w", "type": "waveform", "x": 10, "y": 71,
                    "width": 80, "height": 9}],
    })
    split = next(part for part in chain.split(";") if "asplit" in part)
    assert "afade=t=in" in split, "the waveform branch is taken before the fade"


def test_shaping_applies_with_a_music_bed_too():
    from app.services.music_bed import audio_filters

    chains, _ = audio_filters(
        MusicBed(sound_id="x"), 10.0, has_music_input=True,
        voice_gain_db=-2.0, voice_fade_in=0.3, voice_fade_out=0.0,
    )
    joined = ";".join(chains)
    assert "volume=-2.00dB" in joined
    assert "afade=t=in:st=0:d=0.300" in joined


def test_the_loudness_pass_measures_the_shaped_audio():
    """Measuring unshaped audio would set the level for a different signal."""
    import inspect

    from app.services import jobs

    source = inspect.getsource(jobs.measure_loudness)
    assert "voice_gain_db" in source
    assert "parse_scene" in source
