"""Who said what, and how that reaches the screen.

Captions carry the words; on a two-hander they should carry the attribution too,
because a viewer with the sound off cannot tell a question from its answer.

Assignment is manual. `docs/SPEAKERS.md` records the measurements that ruled out
doing it automatically without a trained model — briefly: one person's intonation
varies more across an episode than two people differ from each other, so pitch
and spectrum both fail, and a confidently wrong attribution is worse than none.

The shape here is built so an automatic pass could be dropped in later without
changing anything downstream: segments carry a numeric `speaker_id`, the
transcript carries the names and colours for those numbers, and everything that
renders reads only those.
"""

from __future__ import annotations

from app.services.scene import BRAND

# Colours assigned to speakers in order. Baby blue first because it is the brand
# accent and most clips have one speaker; gold second because it is the most
# distinct thing from it; then two neutrals that still read on a dark plate.
SPEAKER_COLOURS = (
    BRAND["blue"],
    BRAND["gold"],
    "#B39DDB",  # soft violet
    "#8BC7A4",  # muted green
)

MAX_SPEAKERS = len(SPEAKER_COLOURS)
DEFAULT_SPEAKER = 1


def colour_for(speaker_id: int) -> str:
    """The colour a given speaker's captions are tinted."""
    if speaker_id < 1:
        return SPEAKER_COLOURS[0]
    return SPEAKER_COLOURS[(speaker_id - 1) % len(SPEAKER_COLOURS)]


def names_of(transcript: dict | None) -> dict[int, str]:
    """Speaker numbers to display names, defaulting to "Speaker N"."""
    stored = (transcript or {}).get("speaker_names") or {}
    names: dict[int, str] = {}
    for key, value in stored.items():
        try:
            number = int(key)
        except (TypeError, ValueError):
            continue
        text = str(value).strip()
        if text:
            names[number] = text[:40]
    return names


def name_for(transcript: dict | None, speaker_id: int) -> str:
    return names_of(transcript).get(speaker_id, f"Speaker {speaker_id}")


def speaker_ids(transcript: dict | None) -> list[int]:
    """Every speaker appearing in a transcript, in the order they first speak."""
    seen: list[int] = []
    for segment in (transcript or {}).get("segments") or []:
        number = segment_speaker(segment)
        if number not in seen:
            seen.append(number)
    return seen or [DEFAULT_SPEAKER]


def segment_speaker(segment: dict) -> int:
    """The speaker of one segment, tolerating transcripts written before this.

    Older transcripts have only the free-text `speaker` field, which this app
    always filled with "Speaker 1"; anything unparseable is speaker one rather
    than an error, because a missing attribution is not a broken transcript.
    """
    raw = segment.get("speaker_id")
    if raw is not None:
        try:
            number = int(raw)
            if 1 <= number <= MAX_SPEAKERS:
                return number
        except (TypeError, ValueError):
            pass
    label = str(segment.get("speaker") or "")
    digits = "".join(character for character in label if character.isdigit())
    if digits:
        try:
            number = int(digits)
            if 1 <= number <= MAX_SPEAKERS:
                return number
        except ValueError:
            pass
    return DEFAULT_SPEAKER


def assign(transcript: dict, start: float, end: float, speaker_id: int) -> int:
    """Give every segment overlapping a time range to one speaker.

    A range rather than a single line because people speak in turns: tagging one
    sentence and having the rest of the turn follow is the difference between
    this being usable on an hour of audio and being a chore.

    Returns how many segments changed.
    """
    if not 1 <= speaker_id <= MAX_SPEAKERS:
        raise ValueError(f"speaker_id must be 1..{MAX_SPEAKERS}")

    changed = 0
    for segment in transcript.get("segments") or []:
        seg_start = float(segment.get("start", 0.0))
        seg_end = float(segment.get("end", seg_start))
        if seg_end <= start or seg_start >= end:
            continue
        if segment_speaker(segment) != speaker_id:
            changed += 1
        segment["speaker_id"] = speaker_id
        segment["speaker"] = name_for(transcript, speaker_id)
    return changed


def rename(transcript: dict, speaker_id: int, name: str) -> dict:
    """Set a speaker's display name, and refresh the segments that use it."""
    if not 1 <= speaker_id <= MAX_SPEAKERS:
        raise ValueError(f"speaker_id must be 1..{MAX_SPEAKERS}")
    names = transcript.setdefault("speaker_names", {})
    cleaned = str(name).strip()[:40]
    if cleaned:
        names[str(speaker_id)] = cleaned
    else:
        names.pop(str(speaker_id), None)

    for segment in transcript.get("segments") or []:
        if segment_speaker(segment) == speaker_id:
            segment["speaker"] = name_for(transcript, speaker_id)
    return transcript


def summary(transcript: dict | None) -> list[dict]:
    """Speakers in a transcript, for the editor's speaker list."""
    names = names_of(transcript)
    counts: dict[int, int] = {}
    for segment in (transcript or {}).get("segments") or []:
        number = segment_speaker(segment)
        counts[number] = counts.get(number, 0) + 1
    return [
        {
            "id": number,
            "name": names.get(number, f"Speaker {number}"),
            "colour": colour_for(number),
            "segments": counts.get(number, 0),
        }
        for number in speaker_ids(transcript)
    ]


def is_multi_speaker(transcript: dict | None) -> bool:
    """Whether attribution is worth rendering at all.

    A one-voice clip gets no tint and no name prefix: the feature has to cost
    nothing when it is not being used.
    """
    return len(speaker_ids(transcript)) > 1
