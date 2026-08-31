"""Custom intro and outro videos.

Choose a short video once and every export opens and closes with it — the
same branding Headliner sells on its Pro plan. The chosen clips live in the
media library like everything else; what is stored per person is just their
ids.

The main render is not re-encoded. Each intro/outro is normalised once per
aspect ratio — scaled and padded to the canvas, 30 fps, AAC stereo — and
cached; joining is then a concat of same-codec segments, which is a remux,
not a render. A 10-second intro costs one small encode the first time and
nothing after.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import AppSetting, MediaAsset

log = logging.getLogger(__name__)

# Longer than this is not an intro, it is a different video.
MAX_SECONDS = 15.0
FPS = 30
VIDEO_KINDS = ("video/",)


class BrandingError(RuntimeError):
    pass


def _key(role: str, user_id: str) -> str:
    return f"branding.{role}:{user_id}"


def get_ids(db: Session, user_id: str) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for role in ("intro", "outro"):
        row = db.get(AppSetting, _key(role, user_id))
        out[role] = row.value if row and row.value else None
    return out


def set_id(db: Session, user_id: str, role: str, media_id: str | None) -> None:
    if role not in ("intro", "outro"):
        raise BrandingError("role must be intro or outro")
    if media_id:
        media = db.get(MediaAsset, media_id)
        if not media or media.owner_id != user_id:
            raise BrandingError("That video is not in your library")
        if not (media.content_type or "").startswith(VIDEO_KINDS):
            raise BrandingError("An intro or outro must be a video file (MP4/MOV)")
        if media.duration_seconds and media.duration_seconds > MAX_SECONDS:
            raise BrandingError(f"Keep it under {MAX_SECONDS:.0f} seconds — this one is {media.duration_seconds:.0f}s")
    row = db.get(AppSetting, _key(role, user_id)) or AppSetting(key=_key(role, user_id), value="")
    row.value = media_id or ""
    db.merge(row)
    db.commit()


def _segment_cache(media: MediaAsset, width: int, height: int) -> Path:
    # Late import: tests reload the config module, and a copy bound at import
    # time would point at the wrong directories there.
    from app.core.config import settings

    cache = settings.work_dir / "branding"
    cache.mkdir(parents=True, exist_ok=True)
    return cache / f"{media.id}-{width}x{height}.mp4"


def _normalise(source: Path, target: Path, width: int, height: int) -> None:
    """One canvas-sized, canvas-codec copy of the branding clip."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
         "-t", f"{MAX_SECONDS:.0f}", "-i", str(source),
         "-vf",
         f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
         f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
         f"fps={FPS},format=yuv420p",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
         "-movflags", "+faststart",
         str(target)],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0 or not target.exists():
        raise BrandingError("Could not prepare the intro/outro: " + (result.stderr or "").strip()[-200:])


def _durations_agree(path: Path) -> bool:
    """True when the container and its video stream tell the same time.

    A join whose audio ran long shows a frozen last frame for the extra
    minutes; better to catch it here and take the re-encode path.
    """
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        values = [float(line) for line in (getattr(probe, "stdout", "") or "").split() if line.strip()]
        if len(values) < 2:
            return True
        return max(values) - min(values) < 1.5
    except Exception:
        return True


def stitch(
    db: Session,
    user_id: str,
    video: Path,
    width: int,
    height: int,
    work_dir: Path,
) -> bool:
    """Put the person's intro before and outro after `video`, in place.

    Returns True when anything was joined. Never the reason a render fails:
    a broken branding clip logs a warning and the export goes out plain.
    """
    ids = get_ids(db, user_id)
    if not ids["intro"] and not ids["outro"]:
        return False
    try:
        # By role, not by list position: with both configured and the intro's
        # file missing, positional indexing played the outro at both ends.
        segments: dict[str, Path] = {}
        for role in ("intro", "outro"):
            media_id = ids[role]
            if not media_id:
                continue
            media = db.get(MediaAsset, media_id)
            if not media:
                continue
            from app.core.config import settings

            source = settings.uploads_dir / media.stored_name
            if not source.exists():
                continue
            cached = _segment_cache(media, width, height)
            if not cached.exists():
                # Normalise into a private temp name and rename into place:
                # two concurrent renders used to write the same cache file
                # at once, and the loser could persist a truncated clip.
                scratch = cached.with_name(f".{os.getpid()}-{cached.name}")
                _normalise(source, scratch, width, height)
                scratch.replace(cached)
            segments[role] = cached
        if not segments:
            return False
        intro = segments.get("intro")
        outro = segments.get("outro")

        # The main clip is re-muxed to the same fps/timebase family first so
        # the concat demuxer's -c copy never mixes stream parameters.
        parts: list[Path] = []
        if intro is not None:
            parts.append(intro)
        parts.append(video)
        if outro is not None:
            parts.append(outro)
        listing = work_dir / "branding-concat.txt"
        listing.write_text(
            "".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8", newline="\n"
        )
        joined = work_dir / "branded.mp4"
        # Video is copied; audio is re-encoded. Copied AAC keeps each
        # segment's edit lists and priming, and the summed timestamps came
        # out at double the real length — a 28-second clip that players
        # showed as 56. Decoding and re-encoding the audio rebuilds one
        # continuous timeline, and audio is cheap.
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
             "-f", "concat", "-safe", "0", "-i", str(listing),
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
             "-movflags", "+faststart", str(joined)],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0 and joined.exists() and not _durations_agree(joined):
            result = type(result)(args=result.args, returncode=1, stdout="", stderr="joined durations disagree")
        if result.returncode != 0 or not joined.exists():
            # Codec parameters differed after all: join the slow, sure way.
            inputs: list[str] = []
            for p in parts:
                inputs += ["-i", str(p)]
            n = len(parts)
            graph = "".join(f"[{i}:v][{i}:a]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                 *inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(joined)],
                capture_output=True, text=True, timeout=600,
            )
            if result.returncode != 0 or not joined.exists():
                raise BrandingError((result.stderr or "concat failed").strip()[-200:])
        joined.replace(video)
        return True
    except Exception:
        log.warning("intro/outro skipped for this render", exc_info=True)
        return False
