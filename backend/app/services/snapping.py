"""Moving clip boundaries off the middle of a word.

A clip that begins halfway through "important" starts with "-portant". It is the
single most obvious sign that a clip was cut by dragging a handle rather than by
listening, and it happens constantly: a handle dragged across a waveform lands
wherever the pointer was, and a suggested clip inherits whatever boundary the
scorer liked.

The fix is to nudge each edge to the nearest gap in speech. This uses the word
timings already stored rather than running FFmpeg's `silencedetect`: the
transcript knows exactly where each word began and ended, which is a better
answer than an amplitude threshold — a quiet word is still a word, and
`silencedetect` would happily cut through one.

Two rules keep this from being annoying:

- It only ever moves an edge by a small amount. A boundary that is nowhere near
  a gap is left alone, because moving it a second and a half is not a snap, it
  is overruling the person doing the editing.
- It prefers to *include* whole words rather than exclude them. Given the choice
  the start moves earlier and the end moves later, so a snap never eats speech.
- It only touches an edge that actually falls **inside** a word. A boundary
  already sitting in a gap is where the editor put it, and moving it — even to
  somewhere marginally tidier — is second-guessing a deliberate choice.
"""

from __future__ import annotations

from dataclasses import dataclass

# How far an edge may be moved. Wide enough to cover a mistimed drag, narrow
# enough that the clip stays where it was asked to be.
TOLERANCE = 0.45

# Speech either side of a gap this long counts as a natural break.
GAP = 0.12

# Padding into the surrounding silence, so a clip does not begin on the very
# first phoneme — which sounds clipped even when it is technically complete.
LEAD_IN = 0.08


@dataclass
class Snapped:
    start: float
    end: float
    moved_start: float = 0.0
    moved_end: float = 0.0

    @property
    def moved(self) -> bool:
        return abs(self.moved_start) > 0.001 or abs(self.moved_end) > 0.001

    def as_dict(self) -> dict:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "moved": self.moved,
            "moved_start": round(self.moved_start, 3),
            "moved_end": round(self.moved_end, 3),
        }


def words_of(transcript: dict | None) -> list[dict]:
    """Every word with a usable timing, in order."""
    if not isinstance(transcript, dict):
        return []
    words: list[dict] = []
    for segment in transcript.get("segments", []):
        for word in segment.get("words") or []:
            start, end = word.get("start"), word.get("end")
            if start is None or end is None:
                continue
            try:
                start, end = float(start), float(end)
            except (TypeError, ValueError):
                continue
            if end >= start:
                words.append({"start": start, "end": end})
    words.sort(key=lambda item: item["start"])
    return words


def _snap_start(start: float, words: list[dict], tolerance: float) -> float:
    """Move a start off the middle of a word, preferring to include it."""
    for index, word in enumerate(words):
        if not (word["start"] < start < word["end"]):
            continue
        # Inside a word: take the whole word rather than half of it. The small
        # lead-in stops the clip opening on the very first phoneme, which sounds
        # clipped even when the word is technically complete — but it can only
        # use silence that is actually there. Without this clamp, contiguous
        # speech pushed the boundary back into the *previous* word, so snapping
        # twice kept walking backwards a word at a time.
        floor = words[index - 1]["end"] if index else 0.0
        candidate = max(floor, word["start"] - LEAD_IN)
        if start - candidate <= tolerance:
            return max(0.0, candidate)
        # Too far back to include it, so begin after it instead.
        if word["end"] - start <= tolerance:
            return word["end"]
        break
    # Not inside a word: the boundary is already in a gap, and where in that gap
    # is the editor's business, not ours.
    return start


def _snap_end(end: float, words: list[dict], tolerance: float) -> float:
    """Move an end off the middle of a word, preferring to finish it."""
    for word in words:
        if not (word["start"] < end < word["end"]):
            continue
        if word["end"] - end <= tolerance:
            return word["end"]
        if end - word["start"] <= tolerance:
            return word["start"]
        break
    return end


def snap(
    transcript: dict | None,
    start: float,
    end: float,
    tolerance: float = TOLERANCE,
    duration: float | None = None,
) -> Snapped:
    """Nudge a clip's edges to the nearest break in speech.

    Returns the original range unchanged when there is no transcript, when the
    edges are already clean, or when the nearest break is further away than the
    tolerance allows.
    """
    words = words_of(transcript)
    if not words or end <= start:
        return Snapped(start=start, end=end)

    snapped_start = _snap_start(start, words, tolerance)
    snapped_end = _snap_end(end, words, tolerance)

    # Never let snapping invert or collapse a clip.
    if snapped_end - snapped_start < 0.5:
        return Snapped(start=start, end=end)
    if duration is not None:
        snapped_end = min(snapped_end, duration)
        snapped_start = max(0.0, min(snapped_start, snapped_end - 0.5))

    return Snapped(
        start=snapped_start,
        end=snapped_end,
        moved_start=snapped_start - start,
        moved_end=snapped_end - end,
    )
