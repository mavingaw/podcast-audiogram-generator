"""Loudness normalisation for exported clips.

Every platform normalises what you upload, and they all normalise *down*: a
clip mastered hot is turned down and arrives sounding flat and lifeless next to
one that was delivered at the right level. A clip mastered quietly is left
quiet. So the level a file is delivered at decides how it sounds in a feed, and
"whatever the source episode happened to be mastered at" is the wrong answer.

This is a two-pass `loudnorm`. The first pass measures the assembled mix, the
second applies a linear gain calculated from that measurement. One pass alone
uses a dynamic mode that pumps on speech, which is exactly the material here.
"""

from __future__ import annotations

import json
import logging
import re

from app.services import cancellation

log = logging.getLogger(__name__)

# EBU R128 targets. -14 LUFS is what the social platforms normalise to, so
# delivering at that level means nothing is turned down on the way in.
SOCIAL_LUFS = -14.0
# True peak, with headroom for the lossy re-encode every platform performs.
TRUE_PEAK = -1.5
# Loudness range. Speech is naturally narrow; this leaves it alone.
LOUDNESS_RANGE = 11.0

_JSON_BLOCK = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.DOTALL)


def measure_filter() -> str:
    """The analysing form of the filter, for the first pass."""
    return (
        f"loudnorm=I={SOCIAL_LUFS}:TP={TRUE_PEAK}:LRA={LOUDNESS_RANGE}"
        f":print_format=json"
    )


def parse_measurement(stderr: str) -> dict | None:
    """Pull loudnorm's JSON summary out of FFmpeg's stderr.

    It is printed among the ordinary logging, and older builds print a second
    brace-delimited block, so the block carrying ``input_i`` is the one wanted.
    """
    match = None
    for match in _JSON_BLOCK.finditer(stderr or ""):
        pass  # keep the last, which is the summary
    if match is None:
        return None
    try:
        measured = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    if not all(key in measured for key in required):
        return None
    # A silent clip measures -inf, which the second pass cannot use.
    if any(_is_infinite(measured[key]) for key in ("input_i", "input_tp", "input_thresh")):
        return None
    return measured


def _is_infinite(value: object) -> bool:
    text = str(value).strip().lower()
    return "inf" in text


def apply_filter(measured: dict) -> str:
    """The correcting form, given a first-pass measurement.

    ``linear=true`` asks for a single gain change across the whole clip rather
    than the dynamic mode, so the speech keeps its own dynamics.
    """
    return (
        f"loudnorm=I={SOCIAL_LUFS}:TP={TRUE_PEAK}:LRA={LOUDNESS_RANGE}"
        f":measured_I={measured['input_i']}"
        f":measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}"
        f":measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}"
        f":linear=true:print_format=summary"
    )


def measure(
    ffmpeg: str,
    input_args: list[str],
    audio_chains: list[str],
    audio_label: str,
    duration: float,
    job_id: str | None = None,
) -> dict | None:
    """Run the measuring pass over the assembled mix.

    Measured cost: about two seconds on a fifteen-second clip, taking a render
    from roughly four seconds to six. That is a real tax, and worth it — the
    same clip went from -32.3 LUFS to -14.2 against a -14.0 target, which is
    the difference between a clip the platform turns down and one it leaves
    alone.

    A failure here is not a failure of the render: the caller falls back to
    leaving the audio alone, and the failure is logged rather than swallowed.
    """
    chains = list(audio_chains)
    # The source is split so the waveform can be drawn from its own branch.
    # FFmpeg refuses a graph with an unconnected filter output, so the branch
    # this pass does not use has to be terminated rather than ignored.
    if "[wavesrc]" in ";".join(chains):
        chains.append("[wavesrc]anullsink")
    chains.append(f"{audio_label}{measure_filter()}[loudnorm_probe]")
    command = [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        *input_args,
        "-filter_complex", ";".join(chains),
        "-map", "[loudnorm_probe]",
        "-t", f"{duration:.3f}",
        "-f", "null", "-",
    ]
    try:
        result = cancellation.run(
            job_id, command, capture_output=True, text=True, errors="replace",
            # Same guard the encode itself gets: without one, a wedged ffmpeg
            # parked this render lane until the container restarted.
            timeout=max(120.0, duration * 6),
        )
    except FileNotFoundError:
        log.warning("Loudness measurement skipped: ffmpeg not found")
        return None

    if result.returncode != 0:
        # Falling back keeps the render working, but silently falling back hid
        # a graph error that made this a no-op on every clip. Say so.
        tail = (result.stderr or "").strip().splitlines()[-3:]
        log.warning(
            "Loudness measurement failed (exit %s); clip will keep its source "
            "level. %s", result.returncode, " / ".join(tail),
        )
        return None

    measured = parse_measurement(result.stderr or "")
    if measured is None:
        log.warning("Loudness measurement produced no usable summary")
    return measured
