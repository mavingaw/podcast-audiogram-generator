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
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.rss import RssFetchError, _validate_feed_url

log = logging.getLogger(__name__)

# How often a feed is worth re-reading. Fifteen minutes is well inside what any
# host considers polite and far finer than podcast release schedules need.
CHECK_INTERVAL = timedelta(minutes=int(os.getenv("PAS_FEED_INTERVAL_MINUTES", "15")))

# Reading the feed document itself, which is small.
FEED_TIMEOUT = int(os.getenv("PAS_FEED_XML_TIMEOUT", "30"))

# An episode is a big file on somebody else's server.
DOWNLOAD_TIMEOUT = int(os.getenv("PAS_FEED_TIMEOUT", "900"))
MAX_EPISODE_BYTES = int(os.getenv("PAS_FEED_MAX_BYTES", str(2 * 1024 * 1024 * 1024)))

# Only ever import the newest few on first sight of a feed. Subscribing to a
# show with 400 back-episodes should not enqueue 400 transcriptions.
FIRST_RUN_LIMIT = int(os.getenv("PAS_FEED_FIRST_RUN", "1"))

AUDIO_TYPES = ("audio/", "video/")


class FeedError(RuntimeError):
    pass


@dataclass(frozen=True)
class Soundbite:
    """A moment the podcaster marked as worth pulling out.

    From the Podcasting 2.0 `<podcast:soundbite>` tag. It is the only clip
    suggestion in this application that does not have to be guessed: the person
    who made the episode said which part was the good part, and no amount of
    heuristics or language modelling beats being told.
    """

    start: float
    duration: float
    title: str = ""

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class Episode:
    guid: str
    title: str
    published: str | None
    url: str
    length: int | None = None
    # Empty for the great majority of feeds; the tag is young and few hosts
    # write it. Nothing depends on its presence.
    soundbites: tuple[Soundbite, ...] = ()
    # Episode artwork, when the feed gives the episode its own.
    image_url: str | None = None


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


# A feed document. Bigger than this is not a podcast feed.
MAX_FEED_BYTES = int(os.getenv("PAS_FEED_MAX_XML_BYTES", str(32 * 1024 * 1024)))


def fetch(url: str, etag: str | None = None, modified: str | None = None):
    """Read a feed, using a conditional GET when we have the tokens for one.

    Returns (parsed, etag, modified, changed, raw). `changed` is False when the
    host answered 304, which is the whole point of sending the tokens, and
    `raw` is then None because nothing was sent.

    The transfer is done here rather than left to feedparser so that the raw
    bytes survive. feedparser flattens repeated namespaced elements, which
    loses all but the last `<podcast:soundbite>` on an episode, and those are
    the best clip suggestions available — the podcaster's own. Doing our own
    request also means the URL goes through `_validate_feed_url` and is
    actually fetched under that check, rather than validated here and fetched
    again inside a library.
    """
    import feedparser

    _validate_feed_url(url)

    headers = {"User-Agent": "Kinder/1.0", "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8"}
    if etag:
        headers["If-None-Match"] = etag
    if modified:
        headers["If-Modified-Since"] = modified

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=FEED_TIMEOUT) as response:
            raw = response.read(MAX_FEED_BYTES + 1)
            if len(raw) > MAX_FEED_BYTES:
                raise FeedError("That feed document is implausibly large")
            new_etag = response.headers.get("ETag") or etag
            new_modified = response.headers.get("Last-Modified") or modified
    except urllib.error.HTTPError as error:
        if error.code == 304:
            # Unchanged since last time, which is the polite outcome.
            return None, etag, modified, False, None
        raise FeedError(f"The feed returned HTTP {error.code}") from error
    except FeedError:
        raise
    except Exception as error:
        raise FeedError(f"Could not read the feed: {error}") from error

    try:
        parsed = feedparser.parse(raw)
    except Exception as error:  # feedparser is broad about what it raises
        raise FeedError(f"Could not read the feed: {error}") from error

    # feedparser is forgiving to a fault: handed a page of HTML it returns
    # bozo=False with no entries and an empty `version`, which is how "this is
    # not a feed at all" is distinguished from "this feed has no episodes yet".
    # A new show with an empty feed is legitimate and must still be watchable.
    if not parsed.entries and not getattr(parsed, "version", ""):
        raise FeedError("That URL did not parse as a podcast feed")
    if getattr(parsed, "bozo", 0) and not parsed.entries:
        raise FeedError("That URL did not parse as a podcast feed")

    return parsed, new_etag, new_modified, True, raw


def episodes_of(parsed, raw: bytes | str | None = None) -> list[Episode]:
    """Every entry in a feed that actually carries audio, newest first.

    `raw` is the feed document, when it is available: soundbites are read from
    it because feedparser cannot keep more than one of them.
    """
    if parsed is None:
        return []
    bites = soundbites_of(raw)
    found: list[Episode] = []
    for entry in parsed.entries or []:
        enclosure = _enclosure(entry)
        if not enclosure:
            continue
        url, length = enclosure
        guid = episode_id(entry)
        found.append(
            Episode(
                guid=guid,
                title=(entry.get("title") or "Episode")[:400],
                published=(entry.get("published") or entry.get("updated") or None),
                url=url,
                length=length,
                # Keyed on the feed's own guid element, which is what
                # `episode_id` prefers too, so the two agree for any feed that
                # has one. A feed without guids gets no soundbites rather than
                # the wrong episode's.
                soundbites=bites.get(guid, ()),
                image_url=_image_href(entry.get("image")),
            )
        )
    return found


def _image_href(value) -> str | None:
    """feedparser gives <itunes:image href> as {'href': ...} and <image><url>
    on the channel as {'url': ...}. Either is an image."""
    if isinstance(value, dict):
        href = value.get("href") or value.get("url")
        if href and str(href).startswith(("http://", "https://")):
            return str(href)[:2048]
    elif isinstance(value, str) and value.startswith(("http://", "https://")):
        return value[:2048]
    return None


def artwork_of(parsed) -> str | None:
    """The show's artwork URL, from the channel.

    <itunes:image> is what every podcast app shows and is present on nearly
    every feed; <image><url> is the older RSS element and the fallback.
    """
    if parsed is None:
        return None
    feed = getattr(parsed, "feed", None)
    if feed is None:
        return None
    value = feed.get("image") if hasattr(feed, "get") else getattr(feed, "image", None)
    return _image_href(value)


# A logo. Anything bigger than this is not one.
MAX_ARTWORK_BYTES = int(os.getenv("PAS_ARTWORK_MAX_BYTES", str(24 * 1024 * 1024)))
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


def download_image(url: str, target_dir: Path) -> tuple[str, str, int]:
    """Fetch artwork to disk. Returns (filename, content_type, size).

    The extension comes from the Content-Type, not the URL: feeds routinely
    serve a JPEG from a path ending in .png, or from a CDN path with no
    extension at all, and the renderer reads the file by its suffix.
    """
    import uuid

    _validate_feed_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "Kinder/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=FEED_TIMEOUT) as response:
            content_type = (
                (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            )
            suffix = IMAGE_TYPES.get(content_type)
            if suffix is None:
                raise FeedError(
                    f"Artwork is not an image ({content_type or 'unknown type'})"
                )
            data = response.read(MAX_ARTWORK_BYTES + 1)
    except FeedError:
        raise
    except Exception as error:
        raise FeedError(f"Could not fetch the artwork: {error}") from error
    if len(data) > MAX_ARTWORK_BYTES:
        raise FeedError("Artwork is implausibly large")
    if not data:
        raise FeedError("Artwork was empty")

    name = f"{uuid.uuid4()}{suffix}"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / name).write_bytes(data)
    return name, content_type, len(data)


# The Podcasting 2.0 namespace, which is written both ways in the wild: the
# specification moved to https and plenty of feeds still declare http.
PODCAST_NS = (
    "https://podcastindex.org/namespace/1.0",
    "http://podcastindex.org/namespace/1.0",
)


def _seconds(value) -> float | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds == seconds and seconds >= 0 else None


def soundbites_of(raw: bytes | str | None) -> dict[str, tuple[Soundbite, ...]]:
    """Read every `<podcast:soundbite>` out of a feed, keyed by episode GUID.

    Parsed from the XML rather than taken from feedparser, which flattens
    repeated elements: given three soundbites on an episode it keeps the last
    one and discards its text. Since the entire value of the tag is that the
    podcaster marked *the moments*, keeping one of them is not much better than
    keeping none.

    A malformed feed yields nothing rather than raising. Soundbites are a bonus
    on top of a feed that is otherwise working, and failing an import over an
    optional tag would be a poor trade.
    """
    if not raw:
        return {}
    try:
        from xml.etree import ElementTree

        root = ElementTree.fromstring(raw)
    except Exception:
        log.debug("could not parse feed XML for soundbites", exc_info=True)
        return {}

    found: dict[str, tuple[Soundbite, ...]] = {}
    for item in root.iter("item"):
        guid = (item.findtext("guid") or item.findtext("link") or "").strip()
        if not guid:
            continue
        bites: list[Soundbite] = []
        for child in item:
            tag = child.tag
            if "}" in tag:
                namespace, _, name = tag.partition("}")
                if namespace.lstrip("{") not in PODCAST_NS:
                    continue
            else:
                name = tag
            if name != "soundbite":
                continue
            start = _seconds(child.get("startTime"))
            duration = _seconds(child.get("duration"))
            # Both are required by the specification, and a soundbite without
            # them cannot be turned into a clip.
            if start is None or duration is None or duration <= 0:
                continue
            bites.append(
                Soundbite(start, duration, (child.text or "").strip()[:200])
            )
        if bites:
            found[guid] = tuple(bites)
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
