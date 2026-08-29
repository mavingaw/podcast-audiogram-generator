"""The clip's own audio for the Studio player.

Studio used to play the full episode file and seek into it. From outside the
LAN that meant pulling a 90 MB MP3 through the tunnel before the first second
of preview would play, and the scrubber ran over ninety minutes of audio the
clip did not contain. This cuts the clip out server-side — a 45-second clip
is well under a megabyte — cached per (media, start, end).

Not the render: no transcript cuts, no music, no loudness pass. Those are
what the export is for; this is what you scrub.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path

from app.core.config import settings

log = logging.getLogger(__name__)


def cache_path(media_id: str, start: float, end: float) -> Path:
    cache = settings.work_dir / "previews"
    cache.mkdir(parents=True, exist_ok=True)
    return cache / f"{media_id}-{start:.3f}-{end:.3f}.m4a"


# One cut per clip at a time. The background warm-up and an on-demand
# request for the same clip used to race: both wrote the same temp file and
# one renamed it while the other still had it open.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def ensure(source: Path, media_id: str, start: float, end: float) -> Path:
    """Cut the clip if it is not already cached; return the file."""
    start = max(0.0, float(start))
    end = max(start + 0.5, float(end))
    target = cache_path(media_id, start, end)
    if target.exists():
        return target
    with _lock_for(target.name):
        if target.exists():
            return target
        return _cut(source, start, end, target)


def _cut(source: Path, start: float, end: float, target: Path) -> Path:
    partial = target.with_name(f"{target.stem}.{threading.get_ident()}.part.m4a")
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
         "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(source),
         "-vn", "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-movflags", "+faststart",
         str(partial)],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0 or not partial.exists():
        raise RuntimeError(
            "Could not cut the preview: " + (result.stderr or "ffmpeg failed").strip()[-300:]
        )
    partial.replace(target)
    return target


def warm(source: Path, media_id: str, start: float, end: float) -> None:
    """Cut in the background, so Studio finds the file already there.

    Called when a clip's range is saved. The first open of a clip otherwise
    waits for the cut — a few seconds for a long clip — which is exactly the
    moment somebody has just pressed Open in Studio and is watching.
    """
    def run() -> None:
        try:
            ensure(source, media_id, start, end)
        except Exception as error:
            # The route will try again on demand and report properly.
            log.info("preview warm-up skipped: %s", error)

    threading.Thread(target=run, name="preview-warm", daemon=True).start()
