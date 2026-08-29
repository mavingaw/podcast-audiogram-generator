"""Watching podcast feeds, and turning new episodes into clips.

This is the loop closing: an episode publishes, and by the time anybody looks
there are clips waiting. Everything it needs already exists — transcription,
clip finding, snapping, batch rendering — this only supplies the trigger.

Two rules shape it, and both are about not being alarming:

**Nothing is published automatically.** Clips are prepared and left for a person
to look at. Creators are, reasonably, frightened of software posting a badly cut
clip to their brand account, and "we ask first" is worth more than the minutes it
saves. Rendering is opt-in per feed for the same reason.

**Feeds are polled politely.** Conditional GETs with the etag and modified date
the host gave us, a sane interval, and an episode is imported once and never
again — identity comes from the feed's own GUID rather than from a date, because
plenty of feeds backfill.
"""

from __future__ import annotations

import logging
import os
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.rss import RssFetchError, _validate_feed_url

log = logging.getLogger(__name__)

# How often a feed is worth re-reading. Fifteen minutes is well inside what any
# host considers polite and far finer than podcast release schedules need.
CHECK_INTERVAL = timedelta(minutes=int(os.getenv("PAS_FEED_INTERVAL_MINUTES", "15")))

# An episode is a big file on somebody else's server.
DOWNLOAD_TIMEOUT = int(os.getenv("PAS_FEED_TIMEOUT", "900"))
MAX_EPISODE_BYTES = int(os.getenv("PAS_FEED_MAX_BYTES", str(2 * 1024 * 1024 * 1024)))

# Only ever import the newest few on first sight of a feed. Subscribing to a
# show with 400 back-episodes should not enqueue 400 transcriptions.
FIRST_RUN_LIMIT = int(os.getenv("PAS_FEED_FIRST_RUN", "1"))

AUDIO_TYPES = ("audio/", "video/")


class FeedError(RuntimeError):
    pass


@dataclass
class Episode:
    guid: str
    title: str
    published: str | None
    url: str
    length: int | None = None


def _enclosure(entry) -> tuple[str, int | None] | None:
    """The audio file an entry points at, if it has one."""
    for link in entry.get("enclosures") or []:
        href = (link.get("href") or "").strip()
        kind = (link.get("type") or "").lower()
        if href and (not kind or kind.startswith(AUDIO_TYPES)):
            try:
                length = int(link.get("length") or 0) or None
            except (TypeError, ValueError):
                length = None
            return href, length
    # Some feeds put the file in a plain link with a type instead.
    for link in entry.get("links") or []:
        href = (link.get("href") or "").strip()
        kind = (link.get("type") or "").lower()
        if href and kind.startswith(AUDIO_TYPES):
            return href, None
    return None


def episode_id(entry) -> str:
    """A stable identity for an episode.

    The feed's own GUID first: dates are unreliable because feeds backfill, and
    titles change when somebody fixes a typo. The enclosure URL is the last
    resort, and is at least stable for a given file.
    """
    for key in ("id", "guid"):
        value = (entry.get(key) or "").strip()
        if value:
            return value[:512]
    enclosure = _enclosure(entry)
    if enclosure:
        return enclosure[0][:512]
    return (entry.get("title") or "")[:512]


def fetch(url: str, etag: str | None = None, modified: str | None = None):
    """Read a feed, using a conditional GET when we have the tokens for one.

    Returns (parsed, etag, modified, changed). `changed` is False when the host
    answered 304, which is the whole point of sending the tokens.
    """
    import feedparser

    _validate_feed_url(url)
    try:
        parsed = feedparser.parse(url, etag=etag, modified=modified)
    except Exception as error:  # feedparser is broad about what it raises
        raise FeedError(f"Could not read the feed: {error}") from error

    status = getattr(parsed, "status", None)
    if status == 304:
        return parsed, etag, modified, False
    if status and status >= 400:
        raise FeedError(f"The feed returned HTTP {status}")
    if getattr(parsed, "bozo", 0) and not parsed.entries:
        raise FeedError("That URL did not parse as a podcast feed")

    return (
        parsed,
        getattr(parsed, "etag", None) or etag,
        getattr(parsed, "modified", None) or modified,
        True,
    )


def episodes_of(parsed) -> list[Episode]:
    """Every entry in a feed that actually carries audio, newest first."""
    found: list[Episode] = []
    for entry in parsed.entries or []:
        enclosure = _enclosure(entry)
        if not enclosure:
            continue
        url, length = enclosure
        found.append(
            Episode(
                guid=episode_id(entry),
                title=(entry.get("title") or "Episode")[:400],
                published=(entry.get("published") or entry.get("updated") or None),
                url=url,
                length=length,
            )
        )
    return found


def due(last_checked: datetime | None, interval: timedelta = CHECK_INTERVAL) -> bool:
    """Whether a feed is ready to be read again."""
    if last_checked is None:
        return True
    reference = last_checked
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - reference >= interval


def download(url: str, target: Path, on_progress=None) -> int:
    """Fetch an episode's audio to disk.

    Written to a temporary name and moved into place, so an interrupted download
    never leaves a truncated file that looks importable.
    """
    _validate_feed_url(url)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")

    request = urllib.request.Request(url, headers={"User-Agent": "Kinder/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
            declared = int(response.headers.get("Content-Length") or 0)
            if declared and declared > MAX_EPISODE_BYTES:
                raise FeedError(
                    f"Episode is {declared // (1024 * 1024)}MB, over the "
                    f"{MAX_EPISODE_BYTES // (1024 * 1024)}MB limit"
                )
            written = 0
            with partial.open("wb") as handle:
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    written += len(chunk)
                    # Hosts do not always declare a length, so the cap is
                    # enforced against what actually arrives as well.
                    if written > MAX_EPISODE_BYTES:
                        raise FeedError("Episode exceeded the size limit while downloading")
                    handle.write(chunk)
                    if on_progress and declared:
                        on_progress(written / declared)
    except FeedError:
        partial.unlink(missing_ok=True)
        raise
    except Exception as error:
        partial.unlink(missing_ok=True)
        raise FeedError(f"Could not download the episode: {error}") from error

    partial.replace(target)
    return written


def filename_for(episode: Episode) -> str:
    """A tidy filename for an episode, keeping its real extension."""
    suffix = Path(episode.url.split("?")[0]).suffix.lower()
    if suffix not in {".mp3", ".m4a", ".mp4", ".wav", ".aac", ".ogg", ".flac", ".opus"}:
        suffix = ".mp3"
    safe = "".join(
        character if character.isalnum() or character in " -_" else "_"
        for character in episode.title
    ).strip()
    return f"{(safe or 'episode')[:80]}{suffix}"
