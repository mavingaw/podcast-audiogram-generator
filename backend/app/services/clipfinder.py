"""Finding the clips worth posting.

Scrubbing a two-hour episode for the thirty seconds that will travel is the
slowest part of making these, and it is the part a machine can genuinely help
with: the transcript already knows where sentences end, where the speaker
paused, and how fast they were talking, and the stored peak envelope knows
where the energy is.

This is deliberately not an LLM. Everything here runs on data already in the
database, in milliseconds, with no model to host and no tokens to buy — so
suggestions can be recomputed whenever a transcript changes. An LLM pass would
grade *meaning*, which these signals cannot, and is the obvious next layer; the
scores below are built to be a candidate generator for exactly that.

Every score carries the reasons that produced it, because a suggestion you
cannot interrogate is one you cannot trust or tune.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# A clip has to be long enough to say something and short enough to be watched.
MIN_SECONDS = 12.0
MAX_SECONDS = 75.0
TARGET_SECONDS = 35.0

# A pause this long is a natural place to cut.
PAUSE_SECONDS = 0.7
# Sentence-ending punctuation Whisper emits.
SENTENCE_END = re.compile(r"[.!?]['\"”’)]*$")

# Openings that tend to carry a hook: a claim, a story, or a contradiction.
HOOK_OPENERS = (
    "the thing", "here's", "heres", "what people", "nobody", "everyone",
    "most people", "the truth", "i think", "i used to", "the problem",
    "the reason", "what i", "the biggest", "you don't", "you dont",
    "it turns out", "the first time", "one of the", "the worst",
    "actually", "honestly", "look", "listen",
)

# Words that make a line depend on something said before it, so a clip that
# opens with one is missing its own context.
DANGLING_OPENERS = (
    "it", "that", "this", "they", "them", "those", "these", "he", "she",
    "so", "and", "but", "then", "which", "because", "also", "there",
)

# Filler that suggests the speaker was still finding the thought.
FILLER = ("um", "uh", "erm", "like", "you know", "i mean", "sort of", "kind of")


@dataclass
class Candidate:
    """One suggested clip, with the reasoning that produced its score."""

    start: float
    end: float
    text: str
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def as_dict(self) -> dict:
        return {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "duration": round(self.duration, 2),
            "text": self.text,
            "score": round(self.score, 3),
            "reasons": self.reasons,
            "warnings": self.warnings,
            "title": suggested_title(self.text),
        }


def _words(transcript: dict) -> list[dict]:
    """Every word in the transcript, in order, with absolute timings."""
    words: list[dict] = []
    for segment in transcript.get("segments", []):
        for word in segment.get("words") or []:
            if word.get("start") is None or word.get("end") is None:
                continue
            text = str(word.get("text", "")).strip()
            if text:
                words.append(
                    {"text": text, "start": float(word["start"]), "end": float(word["end"])}
                )
    return words


def _boundaries(words: list[dict]) -> list[int]:
    """Word indices where a clip may reasonably begin.

    A sentence end or a real pause. Cutting anywhere else lands mid-thought,
    which is the tell of a tool that does not understand what it is cutting.
    """
    starts = {0}
    for index, word in enumerate(words[:-1]):
        gap = words[index + 1]["start"] - word["end"]
        if SENTENCE_END.search(word["text"]) or gap >= PAUSE_SECONDS:
            starts.add(index + 1)
    return sorted(starts)


def _energy(peaks: list[float], duration: float, start: float, end: float) -> float:
    """Mean level of the stored envelope over a window, 0..1."""
    if not peaks or duration <= 0:
        return 0.0
    first = max(0, int(len(peaks) * (start / duration)))
    last = min(len(peaks), max(first + 1, int(len(peaks) * (end / duration))))
    window = peaks[first:last]
    return sum(window) / len(window) if window else 0.0


def _variance(peaks: list[float], duration: float, start: float, end: float) -> float:
    """How much the level moves within a window.

    A flat stretch is usually one person reading; movement usually means
    exchange, emphasis or laughter.
    """
    if not peaks or duration <= 0:
        return 0.0
    first = max(0, int(len(peaks) * (start / duration)))
    last = min(len(peaks), max(first + 1, int(len(peaks) * (end / duration))))
    window = peaks[first:last]
    if len(window) < 2:
        return 0.0
    mean = sum(window) / len(window)
    return math.sqrt(sum((value - mean) ** 2 for value in window) / len(window))


def score(candidate: Candidate, words: list[dict], peaks: list[float], duration: float) -> None:
    """Grade a candidate and record why.

    Weights are deliberately small and additive so no single signal decides an
    answer, and so the reasons a clip surfaced stay legible.
    """
    text = candidate.text.strip()
    lowered = text.lower()

    # --- Length -----------------------------------------------------------
    # A gentle preference for something near the target rather than a cliff.
    distance = abs(candidate.duration - TARGET_SECONDS) / TARGET_SECONDS
    candidate.score += max(0.0, 0.30 * (1.0 - distance))
    if MIN_SECONDS <= candidate.duration <= MAX_SECONDS:
        candidate.reasons.append(f"{candidate.duration:.0f}s, a postable length")

    # --- The hook ---------------------------------------------------------
    opener = lowered[:40]
    if any(opener.startswith(phrase) for phrase in HOOK_OPENERS):
        candidate.score += 0.25
        candidate.reasons.append("opens with a hook")

    first_word = lowered.split()[0].strip(",.!?") if lowered.split() else ""
    if first_word in DANGLING_OPENERS:
        candidate.score -= 0.30
        candidate.warnings.append(f"opens on “{first_word}”, which refers back")

    # --- Self-contained ---------------------------------------------------
    if SENTENCE_END.search(text):
        candidate.score += 0.20
        candidate.reasons.append("ends on a complete sentence")
    else:
        candidate.score -= 0.15
        candidate.warnings.append("does not end on a full stop")

    if "?" in text:
        candidate.score += 0.10
        candidate.reasons.append("contains a question")

    # --- Delivery ---------------------------------------------------------
    filler_hits = sum(lowered.count(word) for word in FILLER)
    words_in_clip = max(1, len(lowered.split()))
    filler_rate = filler_hits / words_in_clip
    if filler_rate > 0.06:
        candidate.score -= 0.20
        candidate.warnings.append("heavy on filler words")
    elif filler_rate < 0.02:
        candidate.score += 0.10
        candidate.reasons.append("clean delivery")

    rate = words_in_clip / max(1.0, candidate.duration)
    # Roughly 2.3–3.3 words a second is animated conversational speech.
    if 2.3 <= rate <= 3.3:
        candidate.score += 0.10
        candidate.reasons.append("lively pace")
    elif rate < 1.6:
        candidate.score -= 0.10
        candidate.warnings.append("slow, with long gaps")

    # --- Audio ------------------------------------------------------------
    energy = _energy(peaks, duration, candidate.start, candidate.end)
    if energy > 0.35:
        candidate.score += 0.15
        candidate.reasons.append("strong audio energy")
    variance = _variance(peaks, duration, candidate.start, candidate.end)
    if variance > 0.12:
        candidate.score += 0.15
        candidate.reasons.append("dynamic delivery")

    # --- Cut points -------------------------------------------------------
    # Silence either side means the cut will not clip a word.
    if _pause_before(candidate.start, words) >= PAUSE_SECONDS:
        candidate.score += 0.10
        candidate.reasons.append("clean entry")
    if _pause_after(candidate.end, words) >= PAUSE_SECONDS:
        candidate.score += 0.10
        candidate.reasons.append("clean exit")


def _pause_before(start: float, words: list[dict]) -> float:
    previous = [word for word in words if word["end"] <= start + 0.01]
    return start - previous[-1]["end"] if previous else PAUSE_SECONDS


def _pause_after(end: float, words: list[dict]) -> float:
    following = [word for word in words if word["start"] >= end - 0.01]
    return following[0]["start"] - end if following else PAUSE_SECONDS


def suggested_title(text: str) -> str:
    """A short title from the clip's own first sentence."""
    sentence = re.split(r"(?<=[.!?])\s", text.strip())[0].strip()
    words = sentence.split()
    title = " ".join(words[:9]).rstrip(",;:").rstrip(".")
    if len(words) > 9:
        title += "…"
    return title[:1].upper() + title[1:] if title else "Untitled clip"


def _overlaps(candidate: Candidate, chosen: list[Candidate], allowed: float = 0.35) -> bool:
    """Whether a candidate repeats one already chosen.

    Ten suggestions that are the same moment cut ten ways is not ten choices.
    """
    for other in chosen:
        overlap = min(candidate.end, other.end) - max(candidate.start, other.start)
        if overlap <= 0:
            continue
        shorter = min(candidate.duration, other.duration) or 1.0
        if overlap / shorter > allowed:
            return True
    return False


def find(
    transcript: dict,
    peaks: list[float] | None = None,
    duration: float | None = None,
    limit: int = 8,
) -> list[dict]:
    """Suggest clips worth cutting, best first.

    Candidates are grown from every natural boundary out to every later
    boundary that lands inside the length window, scored, then filtered so the
    list is a set of distinct moments rather than one moment cut many ways.
    """
    words = _words(transcript)
    if len(words) < 8:
        return []

    total = duration or float(transcript.get("duration") or words[-1]["end"])
    peaks = peaks or []
    starts = _boundaries(words)
    start_set = set(starts)

    candidates: list[Candidate] = []
    for position, first in enumerate(starts):
        # Only consider ends that are themselves natural boundaries.
        for last in range(first + 1, len(words)):
            end_time = words[last]["end"]
            span = end_time - words[first]["start"]
            if span < MIN_SECONDS:
                continue
            if span > MAX_SECONDS:
                break
            if last + 1 not in start_set and last != len(words) - 1:
                continue
            candidate = Candidate(
                start=words[first]["start"],
                end=end_time,
                text=" ".join(word["text"] for word in words[first : last + 1]),
            )
            score(candidate, words, peaks, total)
            candidates.append(candidate)

    candidates.sort(key=lambda item: item.score, reverse=True)

    chosen: list[Candidate] = []
    for candidate in candidates:
        if _overlaps(candidate, chosen):
            continue
        chosen.append(candidate)
        if len(chosen) >= limit:
            break
    return [candidate.as_dict() for candidate in chosen]
