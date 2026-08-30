"""Text that fills itself in from the episode.

A template is only reusable if the words on it change with the clip. Put
`{{episode}}` on a text layer, save it as a template, point a feed at it, and
every clip the watcher cuts comes out titled — instead of forty clips all
reading "Episode title here" because nobody opened them one at a time.

The tokens resolve against whatever the clip actually knows: the feed and
episode it came from, the media it was cut out of, and the clip's own position.
An uploaded file has no feed, so the feed tokens are empty for it — which is
the whole reason an unresolved token disappears rather than being left on
screen. A clip that says `{{show}}` in the corner is worse than one that says
nothing there.
"""

from __future__ import annotations

import re
from datetime import datetime

# `{{ name }}` with any spacing. Deliberately not a general expression syntax:
# this is a label on a video, and a template language would be a liability in
# a string that goes through FFmpeg's drawtext escaping.
PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

# What each token means, shown in the editor so the set is discoverable rather
# than folklore. Keep in step with TOKENS in frontend/src/DesignPanel.tsx.
TOKENS = {
    "episode": "Episode title, from the feed",
    "show": "Show name, from the feed",
    "date": "Episode publication date",
    "speaker": "Who is speaking at the start of the clip",
    "title": "The clip's own title",
    "source": "Original file name",
    "timecode": "Where the clip starts in the episode, as m:ss",
    "duration": "Clip length in seconds, rounded",
}


def _timecode(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _date(published: str | None) -> str:
    """A publication date a person would write, from whatever the feed gave us.

    RFC 822 is what podcast feeds are supposed to use and mostly do. Anything
    unparseable is passed through rather than dropped: a feed's own rendering
    of its date is better than nothing, and this is a caption, not a database.
    """
    if not published:
        return ""
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(published.strip(), fmt).strftime("%d %B %Y")
        except ValueError:
            continue
    return published.strip()


def context_for(
    project=None,
    media=None,
    episode=None,
    feed=None,
    transcript: dict | None = None,
    clip_start: float = 0.0,
    duration: float = 0.0,
) -> dict[str, str]:
    """Everything the tokens can resolve to, for one clip."""
    speaker = ""
    if transcript:
        for segment in transcript.get("segments", []):
            try:
                if float(segment.get("end", 0)) > clip_start:
                    speaker = str(segment.get("speaker") or "")
                    break
            except (TypeError, ValueError):
                continue

    return {
        # A feed gives the episode its title. An uploaded file has only its
        # name, and the project is usually named after it; either beats an
        # empty title layer on every clip cut from an upload.
        "episode": (
            str(getattr(episode, "title", "") or "")
            or str(getattr(project, "title", "") or "")
            or _basename(getattr(media, "original_name", "") or "")
        ),
        "show": str(getattr(feed, "title", "") or ""),
        "date": _date(getattr(episode, "published", None)),
        "speaker": speaker,
        "title": str(getattr(project, "title", "") or ""),
        "source": str(getattr(media, "original_name", "") or ""),
        "timecode": _timecode(clip_start),
        "duration": f"{round(duration)}",
    }


def _basename(name: str) -> str:
    """"episode 12.mp3" -> "episode 12"."""
    stem = name.rsplit("/", 1)[-1]
    if "." in stem and len(stem.rsplit(".", 1)[1]) <= 4:
        stem = stem.rsplit(".", 1)[0]
    return stem.strip()


def resolve(text: str, context: dict[str, str]) -> str:
    """Replace every known token, and remove the ones that have no value.

    An unknown token is left alone: someone typing literal braces should see
    what they typed rather than have it silently eaten. A *known* token with
    nothing behind it is removed, because "Episode: " with a blank after it is
    the failure this feature exists to avoid.
    """
    if not text or "{{" not in text:
        return text

    def substitute(match: re.Match) -> str:
        name = match.group(1).lower()
        if name not in context:
            return match.group(0)
        return context[name]

    filled = PATTERN.sub(substitute, text)
    # Tidy what an empty value leaves behind: a dangling separator, or the run
    # of spaces where a word used to be.
    filled = re.sub(r"\s{2,}", " ", filled)
    filled = re.sub(r"^[\s\-–—·:|]+|[\s\-–—·:|]+$", "", filled)
    return filled


def used_in(text: str) -> set[str]:
    """Which known tokens a piece of text refers to."""
    return {
        match.group(1).lower()
        for match in PATTERN.finditer(text or "")
        if match.group(1).lower() in TOKENS
    }
