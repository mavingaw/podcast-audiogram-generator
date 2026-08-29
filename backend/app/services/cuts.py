"""Removing time from the middle of a clip.

The thing that makes transcript editing feel like editing text rather than
audio: strike a sentence out of the transcript and the sentence is gone from
the clip, with what came after it moved up to meet what came before. A tangent,
a phone ringing, forty seconds of throat-clearing before the point — cut, and
the clip is the good part.

Everything here works in two coordinate systems and the difference matters:

  source time   where a word actually is in the uploaded episode
  output time   where it lands in the rendered clip, after the cuts close up

A cut removes source time, so every subsequent moment moves earlier by the
total length cut out before it. Captions, waveform, layer timings and the
render's own duration all have to move together, and they move by walking the
kept spans rather than by subtracting a constant.

The audio is cut in a pre-pass rather than in the render's filter graph. Doing
it in the graph would mean every downstream stage — captions, peaks, layer
timing, the loudness measurement — carrying its own copy of the same mapping,
and the first one to disagree gives you a clip whose captions are half a
sentence behind. A pre-pass produces an ordinary continuous file, and the rest
of the renderer never learns that anything was removed.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Two cuts closer together than this have no audible gap between them, and
# keeping the sliver would put a concat boundary either side of a few
# milliseconds of audio for no reason.
MERGE_GAP = 0.02

# A clip has to be long enough to be a clip.
MIN_REMAINING = 0.5


@dataclass(frozen=True)
class Span:
    start: float
    end: float

    @property
    def length(self) -> float:
        return max(0.0, self.end - self.start)


def parse(raw, clip_start: float, clip_end: float) -> list[Span]:
    """Read cut ranges out of a scene, in source time.

    Stored scenes are not trusted: ranges arrive from a browser, may overlap
    each other after a few edits, may be reversed, and may sit partly or wholly
    outside the clip now being rendered because the clip edges moved after the
    cuts were made. All of that is normalised here so everything downstream can
    assume sorted, disjoint, in-bounds spans.
    """
    if not isinstance(raw, (list, tuple)):
        return []

    spans: list[Span] = []
    for item in raw:
        if isinstance(item, dict):
            start, end = item.get("start"), item.get("end")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            start, end = item[0], item[1]
        else:
            continue
        try:
            start, end = float(start), float(end)
        except (TypeError, ValueError):
            continue
        if start != start or end != end:  # NaN
            continue
        # Reversed by a backwards drag: the range was meant, not the order.
        if end < start:
            start, end = end, start
        start = max(start, clip_start)
        end = min(end, clip_end)
        if end - start > 0.001:
            spans.append(Span(start, end))

    if not spans:
        return []

    spans.sort(key=lambda span: span.start)
    merged = [spans[0]]
    for span in spans[1:]:
        last = merged[-1]
        if span.start <= last.end + MERGE_GAP:
            merged[-1] = Span(last.start, max(last.end, span.end))
        else:
            merged.append(span)
    return merged


def kept(cuts: list[Span], clip_start: float, clip_end: float) -> list[Span]:
    """The spans of source that survive, in order."""
    spans: list[Span] = []
    cursor = clip_start
    for cut in cuts:
        if cut.start > cursor:
            spans.append(Span(cursor, cut.start))
        cursor = max(cursor, cut.end)
    if clip_end > cursor:
        spans.append(Span(cursor, clip_end))
    return [span for span in spans if span.length > 0.001]


def duration(spans: list[Span]) -> float:
    return sum(span.length for span in spans)


def map_time(time: float, spans: list[Span]) -> float | None:
    """Source time to output time, or None if that moment was cut out.

    None is a real answer and callers should say something with it: a caption
    whose word was deleted should not be drawn, and a layer that started inside
    a removed span has to start somewhere else.
    """
    elapsed = 0.0
    for span in spans:
        if time < span.start:
            return None
        if time <= span.end:
            return elapsed + (time - span.start)
        elapsed += span.length
    return None


def map_clamped(time: float, spans: list[Span]) -> float:
    """Source time to output time, snapping a cut moment to the join.

    For edges rather than points. A caption that starts just inside a cut and
    runs out of it should appear at the join, not vanish; the alternative is
    dropping a line of speech that is still audible.
    """
    if not spans:
        return 0.0
    if time <= spans[0].start:
        return 0.0
    elapsed = 0.0
    for span in spans:
        if time < span.start:
            return elapsed  # inside a cut: the join
        if time <= span.end:
            return elapsed + (time - span.start)
        elapsed += span.length
    return elapsed


def remap_transcript(transcript: dict, spans: list[Span]) -> dict:
    """Rewrite a transcript onto the output timeline.

    Words inside a cut are dropped rather than moved, and a segment split by a
    cut becomes the words that are left, with its text rebuilt from them.
    Keeping the original segment text would caption words that are no longer in
    the audio, which is the most confusing possible failure of a feature whose
    whole promise is that the words and the sound agree.

    Segments with no word timings can only be moved whole. They are kept if
    their midpoint survives, which is the best guess available and matches what
    the caption builder does with them anyway.
    """
    from app.services.transcription import join_words

    if not spans:
        return {**transcript, "segments": []}

    out_segments = []
    for segment in transcript.get("segments", []):
        words = segment.get("words") or []
        if words:
            moved = []
            for word in words:
                try:
                    start = float(word.get("start"))
                    end = float(word.get("end"))
                except (TypeError, ValueError):
                    continue
                # A word is kept if its middle survives. Judging by the start
                # alone would keep a word whose sound is mostly gone, and by
                # both edges would drop words that merely touch the cut.
                if map_time((start + end) / 2, spans) is None:
                    continue
                moved.append({
                    **word,
                    "start": round(map_clamped(start, spans), 3),
                    "end": round(map_clamped(end, spans), 3),
                })
            if not moved:
                continue
            # A word that straddled a join can come out zero-length or
            # backwards; give it back the minimum a caption needs.
            for index, word in enumerate(moved):
                if word["end"] <= word["start"]:
                    word["end"] = round(word["start"] + 0.04, 3)
                if index and word["start"] < moved[index - 1]["end"]:
                    word["start"] = moved[index - 1]["end"]
                    word["end"] = max(word["end"], word["start"] + 0.04)
            out_segments.append({
                **segment,
                "start": moved[0]["start"],
                "end": moved[-1]["end"],
                "text": join_words(moved).strip() or segment.get("text", ""),
                "words": moved,
            })
            continue

        try:
            start = float(segment.get("start", 0.0))
            end = float(segment.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        if map_time((start + end) / 2, spans) is None:
            continue
        out_segments.append({
            **segment,
            "start": round(map_clamped(start, spans), 3),
            "end": round(map_clamped(end, spans), 3),
        })

    for index, segment in enumerate(out_segments, start=1):
        segment["id"] = index
    return {**transcript, "duration": duration(spans), "segments": out_segments}


def extract(source: Path, target: Path, spans: list[Span],
            timeout: float = 900.0) -> None:
    """Write the kept spans out as one continuous audio file.

    Decoded once and cut with atrim rather than run as several seeking
    extractions: seeking to a keyframe is not sample-accurate in a compressed
    source, and a cut that lands tens of milliseconds off is audible as a click
    at every join.
    """
    if not spans:
        raise RuntimeError("Every part of this clip has been cut out.")

    filters = []
    for index, span in enumerate(spans):
        filters.append(
            f"[0:a]atrim=start={span.start:.4f}:end={span.end:.4f},"
            f"asetpts=PTS-STARTPTS[c{index}]"
        )
    joins = "".join(f"[c{index}]" for index in range(len(spans)))
    filters.append(f"{joins}concat=n={len(spans)}:v=0:a=1[out]")

    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(source),
        "-filter_complex", ";".join(filters),
        "-map", "[out]",
        # PCM, because this file is read again by the render and by the
        # loudness pass; re-encoding a clip that is about to be encoded again
        # is a generation of loss for no gain.
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
        str(target),
    ]
    logger.info("cutting %d span(s) out of %s", len(spans), source.name)
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0 or not target.exists():
        raise RuntimeError(
            "Could not apply the transcript cuts: "
            + (result.stderr or "ffmpeg failed").strip()[-400:]
        )
