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
import os
import subprocess
import threading
import time
from pathlib import Path

from app.core.config import settings

log = logging.getLogger(__name__)


def cache_path(media_id: str, start: float, end: float, source: Path | None = None) -> Path:
    cache = settings.work_dir / "previews"
    cache.mkdir(parents=True, exist_ok=True)
    # MP3 sources are cut by stream copy, so the cut stays MP3; everything
    # else is re-encoded to AAC in an M4A.
    suffix = ".mp3" if source is not None and source.suffix.lower() == ".mp3" else ".m4a"
    return cache / f"{media_id}-{start:.3f}-{end:.3f}{suffix}"


# One cut per clip at a time. The background warm-up and an on-demand
# request for the same clip used to race: both wrote the same temp file and
# one renamed it while the other still had it open.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


# Previews are a cache. A clip edge nudged ten times leaves ten files behind,
# each under a megabyte, and nothing else ever deletes them.
MAX_AGE_DAYS = float(os.getenv("PAS_PREVIEW_DAYS", "7"))
_last_sweep = 0.0


def sweep(now: float | None = None, max_age_days: float | None = None) -> int:
    """Remove previews nobody has opened for a week. Cheap: one directory listing."""
    now = time.time() if now is None else now
    age_limit = (MAX_AGE_DAYS if max_age_days is None else max_age_days) * 86400
    removed = 0
    cache = settings.work_dir / "previews"
    if not cache.is_dir():
        return 0
    for path in list(cache.glob("*.m4a")) + list(cache.glob("*.mp3")):
        try:
            # atime is unreliable on some mounts; mtime is bumped on every serve
            # below, so it doubles as "last opened".
            if now - path.stat().st_mtime > age_limit:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _sweep_occasionally() -> None:
    global _last_sweep
    now = time.time()
    if now - _last_sweep < 3600:
        return
    _last_sweep = now
    try:
        removed = sweep(now)
        if removed:
            log.info("removed %d stale previews", removed)
    except Exception:
        log.debug("preview sweep failed", exc_info=True)


def ensure(source: Path, media_id: str, start: float, end: float) -> Path:
    """Cut the clip if it is not already cached; return the file."""
    _sweep_occasionally()
    start = max(0.0, float(start))
    end = max(start + 0.5, float(end))
    target = cache_path(media_id, start, end, source)
    if target.exists():
        # Touch it: "last opened" is what the sweep goes by.
        try:
            os.utime(target, None)
        except OSError:
            pass
        return target
    with _lock_for(target.name):
        if target.exists():
            return target
        return _cut(source, start, end, target)


def _cut(source: Path, start: float, end: float, target: Path) -> Path:
    partial = target.with_name(f"{target.stem}.{threading.get_ident()}.part{target.suffix}")
    if target.suffix == ".mp3":
        # The source is already MP3: copy the frames rather than re-encode.
        # Re-encoding a 25-minute clip to AAC took 45 seconds, and the player
        # had given up long before; a copy is a fraction of a second. The
        # cut lands on a frame boundary, ~26 ms — nobody hears that.
        codec = ["-c:a", "copy"]
    else:
        codec = ["-c:a", "aac", "-b:a", "96k", "-ac", "2", "-movflags", "+faststart"]
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
         "-err_detect", "ignore_err",
         "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(source),
         "-vn", *codec, str(partial)],
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
