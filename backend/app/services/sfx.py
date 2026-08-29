"""One-shot sound effects placed at points in a clip.

A riser under the first sentence, a record scratch on the punchline, a
stinger at the end. Each cue is a library sound, a time inside the clip and a
level; the render delays each one to its time and mixes it under the voice
and the music bed, before the loudness pass so the platform-facing level is
the level of the whole mix.

Cues are stored on the scene as ``sfx: [{soundId, at, gainDb}]`` in clip
seconds, the same coordinates the timeline uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MAX_CUES = 16
MIN_GAIN_DB = -30.0
MAX_GAIN_DB = 12.0


@dataclass(frozen=True)
class Cue:
    sound_id: str
    at: float
    gain_db: float = 0.0


def parse(scene: dict | None, duration: float) -> list[Cue]:
    """Read cues out of a scene, bounded and in time order.

    Anything unreadable is dropped rather than failing the render: a cue
    whose sound was deleted from the library is not a reason to lose the
    clip, and a stored scene is not a promise.
    """
    raw = (scene or {}).get("sfx") if isinstance(scene, dict) else None
    if not isinstance(raw, list):
        return []
    cues: list[Cue] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("soundId"):
            continue
        try:
            at = float(item.get("at", 0.0))
            gain = float(item.get("gainDb", 0.0))
        except (TypeError, ValueError):
            continue
        if at != at or at < 0 or at >= duration:
            continue
        gain = max(MIN_GAIN_DB, min(MAX_GAIN_DB, gain))
        cues.append(Cue(str(item["soundId"]), round(at, 3), round(gain, 2)))
        if len(cues) >= MAX_CUES:
            break
    cues.sort(key=lambda cue: cue.at)
    return cues


@dataclass(frozen=True)
class ResolvedCue:
    path: Path
    at: float
    gain_db: float


def filters(resolved: list[ResolvedCue], first_input: int, mix_label: str) -> tuple[list[str], str]:
    """Filter chains that place each cue and fold them into the mix.

    Each cue input is delayed to its time (adelay wants milliseconds, per
    channel), given its level, and then everything is summed with amix at
    unity — ``normalize=0`` — so adding an effect does not quietly turn the
    voice down, which is amix's default and the one thing nobody wants.
    """
    if not resolved:
        return [], mix_label
    chains: list[str] = []
    labels: list[str] = []
    for offset, cue in enumerate(resolved):
        index = first_input + offset
        ms = int(round(cue.at * 1000))
        label = f"[fx{offset}]"
        chains.append(
            f"[{index}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"adelay={ms}|{ms},volume={cue.gain_db:.2f}dB{label}"
        )
        labels.append(label)
    chains.append(
        f"{mix_label}{''.join(labels)}amix=inputs={len(labels) + 1}:"
        f"normalize=0:dropout_transition=0[amixfx]"
    )
    return chains, "[amixfx]"
