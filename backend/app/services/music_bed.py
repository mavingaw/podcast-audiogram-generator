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

Ducking is exact when the clip has a transcript — the bed is dipped by the
number on the slider precisely while someone is speaking, driven by the word
timings — and falls back to FFmpeg's sidechain compressor keyed off the voice
track when it does not.
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
# Relative to the bed's level: a keyframe at 0 dB is the bed as set; -12 is a
# dip. Bounded so a stored scene cannot ask for a blast.
AUTOMATION_RANGE = (-40.0, 6.0)
MAX_KEYFRAMES = 24
# A backslash, for escaping commas inside a filter-option expression.
BS = chr(92)


@dataclass(frozen=True)
class MusicBed:
    sound_id: str
    gain_db: float = DEFAULT_GAIN_DB
    duck_db: float = DEFAULT_DUCK_DB
    fade_in: float = DEFAULT_FADE_IN
    fade_out: float = DEFAULT_FADE_OUT
    start_offset: float = 0.0
    loop: bool = True
    # Level changes over the clip, as (clip seconds, dB) keyframes: the bed
    # rides between them in a straight line. Ducking follows the voice
    # automatically; this is for the deliberate moves — dip for the quote,
    # swell under the outro — that a person places.
    automation: tuple[tuple[float, float], ...] = ()

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
        automation=_automation(raw.get("automation")),
    )


def _automation(raw) -> tuple[tuple[float, float], ...]:
    """Keyframes, in time order, bounded, at most MAX_KEYFRAMES."""
    if not isinstance(raw, list):
        return ()
    points: list[tuple[float, float]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        at = _number(item.get("at"), -1.0)
        gain = _number(item.get("gainDb"), 0.0)
        if at < 0 or at != at:
            continue
        points.append((round(at, 3), round(_clamp(gain, *AUTOMATION_RANGE), 2)))
    points.sort(key=lambda point: point[0])
    return tuple(points[:MAX_KEYFRAMES])


def automation_expression(points, duration: float) -> str | None:
    """A per-frame gain expression for FFmpeg's volume filter.

    dB is interpolated in straight lines between keyframes, held flat before
    the first and after the last, and turned into a linear factor at the end.
    Commas inside the expression are escaped because it sits inside a filter
    option; the whole thing runs with eval=frame so it is re-evaluated as
    time passes rather than once at start.
    """
    points = [(at, gain) for at, gain in points if at <= duration + 0.001]
    if not points:
        return None
    if len(points) == 1:
        db = f"{points[0][1]:.2f}"
    else:
        # Innermost last: the level after the final keyframe.
        db = f"{points[-1][1]:.2f}"
        for (t0, g0), (t1, g1) in reversed(list(zip(points, points[1:]))):
            span = max(0.001, t1 - t0)
            segment = f"({g0:.2f}+({g1 - g0:.2f})*(t-{t0:.3f})/{span:.3f})"
            db = f"if(lt(t{BS},{t1:.3f}){BS},{segment}{BS},{db})"
        db = f"if(lt(t{BS},{points[0][0]:.3f}){BS},{points[0][1]:.2f}{BS},{db})"
    return f"volume='pow(10{BS},({db})/20)':eval=frame"


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


# How ducking driven by the transcript moves. Ramps are short so the bed is
# out of the way by the first syllable, and the release waits long enough
# that a breath between sentences does not pump the bed up and down.
DUCK_ATTACK = 0.12
DUCK_RELEASE = 0.35
DUCK_JOIN_GAP = 0.6


def duck_keyframes(
    speech_spans, duck_db: float, duration: float
) -> tuple[tuple[float, float], ...]:
    """Level keyframes that dip the bed exactly while someone is speaking.

    The sidechain compressor's reduction depends on how far the voice exceeds
    its threshold, so "-12 dB" there was a calibration, not a promise. With
    word timings, the dip can be exactly the number on the slider: down by
    the first word, back up after the last, and gaps shorter than a breath
    are bridged so the bed does not surface between sentences.
    """
    spans = []
    for start, end in sorted((float(a), float(b)) for a, b in speech_spans):
        start, end = max(0.0, start), min(duration, end)
        if end <= start:
            continue
        if spans and start - spans[-1][1] <= DUCK_JOIN_GAP:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    if not spans:
        return ()
    # One expression evaluates every keyframe every frame, so a long, chatty
    # clip is bridged more coarsely until the count is sane: past sixty
    # stretches the difference is inaudible and the graph stays readable.
    gap = DUCK_JOIN_GAP
    while len(spans) > 60:
        gap *= 2
        merged = [list(spans[0])]
        for start, end in spans[1:]:
            if start - merged[-1][1] <= gap:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        spans = merged
    points: list[tuple[float, float]] = []
    for start, end in spans:
        down_at = max(0.0, start - DUCK_ATTACK)
        up_at = min(duration, end + DUCK_RELEASE)
        if points and down_at <= points[-1][0]:
            # Overlapping ramps: stay down rather than blipping up.
            points.pop()
        else:
            points.append((round(down_at, 3), 0.0))
        points.append((round(min(start, up_at), 3), duck_db))
        points.append((round(max(end, down_at), 3), duck_db))
        points.append((round(up_at, 3), 0.0))
    return tuple(points)


def audio_filters(
    bed: MusicBed,
    duration: float,
    has_music_input: bool = True,
    voice_gain_db: float = 0.0,
    voice_fade_in: float = 0.0,
    voice_fade_out: float = 0.0,
    speech_spans=None,
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
    # Two rides can apply: the person's own keyframes, and — when the
    # transcript says where the speech is — an exact dip under it. They are
    # separate volume filters, so each stays a straight line in dB and the
    # combination is their sum.
    ride = automation_expression(bed.automation, duration)
    if ride:
        music += "," + ride
    exact_duck = None
    if bed.ducking_enabled and speech_spans:
        exact_duck = automation_expression(
            duck_keyframes(speech_spans, bed.duck_db, duration), duration
        )
    if exact_duck:
        music += "," + exact_duck
    if fade_in > 0:
        music += f",afade=t=in:st=0:d={fade_in:.3f}"
    if fade_out > 0:
        music += f",afade=t=out:st={max(0.0, duration - fade_out):.3f}:d={fade_out:.3f}"
    chains.append(f"{music}[musicraw]")

    if bed.ducking_enabled and not exact_duck:
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
