"""The music bed layer of a project scene.

A scene stores the bed as one normalised object rather than as an extra media
layer, because it is not positioned on the canvas and it always spans the clip::

    "music": {
      "soundId": "…",
      "gainDb": -18.0,
      "duckDb": -12.0,
      "fadeInSeconds": 1.0,
      "fadeOutSeconds": 2.0,
      "startOffsetSeconds": 0.0,
      "loop": true
    }

Ducking is done with FFmpeg's sidechain compressor keyed off the voice track,
so it works whether or not the clip has a transcript.
"""

from __future__ import annotations

from dataclasses import dataclass

# The bed sits well under speech by default: quiet enough not to compete,
# loud enough to survive platform loudness normalisation.
DEFAULT_GAIN_DB = -18.0
DEFAULT_DUCK_DB = -12.0
DEFAULT_FADE_IN = 1.0
DEFAULT_FADE_OUT = 2.0

GAIN_RANGE = (-40.0, 0.0)
DUCK_RANGE = (-30.0, 0.0)


@dataclass(frozen=True)
class MusicBed:
    sound_id: str
    gain_db: float = DEFAULT_GAIN_DB
    duck_db: float = DEFAULT_DUCK_DB
    fade_in: float = DEFAULT_FADE_IN
    fade_out: float = DEFAULT_FADE_OUT
    start_offset: float = 0.0
    loop: bool = True

    @property
    def ducking_enabled(self) -> bool:
        return self.duck_db < -0.5


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def from_scene(scene: dict | None) -> MusicBed | None:
    """Read the music bed out of a scene, or ``None`` when there isn't one."""
    if not isinstance(scene, dict):
        return None
    raw = scene.get("music")
    if not isinstance(raw, dict):
        return None
    sound_id = raw.get("soundId") or raw.get("sound_id")
    if not sound_id:
        return None
    return MusicBed(
        sound_id=str(sound_id),
        gain_db=_clamp(_number(raw.get("gainDb"), DEFAULT_GAIN_DB), *GAIN_RANGE),
        duck_db=_clamp(_number(raw.get("duckDb"), DEFAULT_DUCK_DB), *DUCK_RANGE),
        fade_in=max(0.0, _number(raw.get("fadeInSeconds"), DEFAULT_FADE_IN)),
        fade_out=max(0.0, _number(raw.get("fadeOutSeconds"), DEFAULT_FADE_OUT)),
        start_offset=max(0.0, _number(raw.get("startOffsetSeconds"), 0.0)),
        loop=bool(raw.get("loop", True)),
    )


def _number(value, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def duck_ratio(duck_db: float) -> float:
    """Compressor ratio approximating a target dip under speech.

    The sidechain compressor's actual reduction depends on how far the voice
    exceeds the threshold, so this is a calibration, not an exact conversion:
    a 12 dB dip maps to roughly 7:1, which is the range mixers reach for.
    """
    return round(_clamp(1.0 + abs(duck_db) / 2.0, 1.0, 20.0), 2)


def voice_shaping(gain_db: float, fade_in: float, fade_out: float, duration: float) -> str:
    """Level and edge treatment for the voice track.

    Returned as filter fragments to append, so the caller does not have to know
    whether anything was asked for: no gain and no fades gives an empty string
    and the chain is unchanged.

    Fades are clamped to the clip. A two-second fade on a 1.5-second clip is
    somebody typing rather than deciding, and FFmpeg would happily render the
    whole thing rising out of silence.
    """
    parts = []
    if abs(gain_db) > 0.01:
        parts.append(f"volume={gain_db:.2f}dB")
    limit = max(0.1, duration / 2)
    fade_in = min(max(0.0, fade_in), limit)
    fade_out = min(max(0.0, fade_out), limit)
    if fade_in > 0.01:
        parts.append(f"afade=t=in:st=0:d={fade_in:.3f}")
    if fade_out > 0.01:
        parts.append(
            f"afade=t=out:st={max(0.0, duration - fade_out):.3f}:d={fade_out:.3f}"
        )
    return ("," + ",".join(parts)) if parts else ""


def audio_filters(
    bed: MusicBed,
    duration: float,
    has_music_input: bool = True,
    voice_gain_db: float = 0.0,
    voice_fade_in: float = 0.0,
    voice_fade_out: float = 0.0,
) -> tuple[list[str], str]:
    """Build the audio half of the render's ``-filter_complex``.

    Returns the filter chain fragments and the label carrying the final mix.
    Input 0 is the source media; input 2, when present, is the music track.
    """
    shaping = voice_shaping(voice_gain_db, voice_fade_in, voice_fade_out, duration)

    if not has_music_input:
        # The split happens after shaping so the drawn waveform matches what is
        # heard: fading the audio but not the picture looks like a bug.
        return (
            ["[0:a]aformat=sample_fmts=fltp:sample_rates=48000:"
             f"channel_layouts=stereo{shaping},asplit=2[aout][wavesrc]"],
            "[aout]",
        )

    fade_in = min(bed.fade_in, duration / 2)
    fade_out = min(bed.fade_out, duration / 2)
    chains = [
        "[0:a]aformat=sample_fmts=fltp:sample_rates=48000:"
        f"channel_layouts=stereo{shaping},asplit=3[voice][wavesrc][duckkey]"
    ]

    music = (
        "[2:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        f"atrim=start={bed.start_offset:.3f},asetpts=PTS-STARTPTS,"
        # apad guarantees the bed outlives the clip even when the track is
        # shorter and looping is off, so amix never ends early.
        f"apad,atrim=duration={duration:.3f},asetpts=PTS-STARTPTS,"
        f"volume={bed.gain_db:.2f}dB"
    )
    if fade_in > 0:
        music += f",afade=t=in:st=0:d={fade_in:.3f}"
    if fade_out > 0:
        music += f",afade=t=out:st={max(0.0, duration - fade_out):.3f}:d={fade_out:.3f}"
    chains.append(f"{music}[musicraw]")

    if bed.ducking_enabled:
        chains.append(
            f"[musicraw][duckkey]sidechaincompress=threshold=0.03:"
            f"ratio={duck_ratio(bed.duck_db)}:attack=20:release=400:"
            f"detection=rms:level_sc=1[music]"
        )
    else:
        chains.append("[duckkey]anullsink")
        chains.append("[musicraw]anull[music]")

    chains.append(
        "[voice][music]amix=inputs=2:duration=first:"
        "dropout_transition=0:normalize=0[aout]"
    )
    return chains, "[aout]"
