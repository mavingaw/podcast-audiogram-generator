"""Uploading a file in pieces, because the way in has a size limit.

Cloudflare's free plan refuses any request body over 100 MB at the edge. The
request never reaches this application: there is no log line, no error handler
of ours runs, and the browser is handed a 413 from a machine in another
country. A podcast episode is routinely larger than that — an hour of 128 kbps
MP3 is about 57 MB, an hour of WAV is over 600 MB — so for anyone outside the
LAN the upload button simply did not work, and did not say why.

The fix is to stop sending one large request. The browser slices the file and
posts each slice separately; each slice is small enough to pass, and this
module puts them back together. A side effect worth having: real progress, and
a failed slice costs one slice rather than the whole upload.

Assembly is append-only into a single file rather than a part file per slice,
so a 600 MB upload needs 600 MB of disk rather than 1.2 GB, and finishing is a
rename rather than a concatenation pass.

Slices must arrive in order. Out-of-order arrival is rejected rather than
buffered: buffering would mean holding arbitrary amounts of a stranger's data
in memory on the promise that the missing piece is coming, and the client this
was written for sends them in order anyway.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.services.storage import (
    ALLOWED_UPLOAD_SUFFIXES,
    UploadValidationError,
    contained_path,
    is_image,
    make_stored_name,
)

# Comfortably under the 100 MB edge limit, with room for the multipart wrapper
# and headers. Smaller would mean more round trips on a fast link; larger risks
# the whole feature on a limit nobody controls.
CHUNK_BYTES = 32 * 1024 * 1024

# An upload nobody has touched for this long is not coming back: a closed tab,
# a laptop lid, a phone that lost signal.
STALE_SECONDS = 6 * 60 * 60


def staging_dir() -> Path:
    path = settings.uploads_dir / ".partial"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Session_:
    id: str
    owner_id: str
    filename: str
    content_type: str
    total_bytes: int
    received: int
    next_index: int

    @property
    def complete(self) -> bool:
        return self.received >= self.total_bytes


def _paths(upload_id: str) -> tuple[Path, Path]:
    # The id is generated here and never taken from the client, but it still
    # goes through the containment check: an id that reached this function from
    # anywhere else must not be able to name a file outside the staging area.
    root = staging_dir()
    return (
        contained_path(root, root / f"{upload_id}.part"),
        contained_path(root, root / f"{upload_id}.json"),
    )


def _read(upload_id: str) -> Session_:
    _, meta_path = _paths(upload_id)
    if not meta_path.exists():
        raise UploadValidationError("This upload has expired. Start it again.", 404)
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    return Session_(**data)


def _write(session: Session_) -> None:
    _, meta_path = _paths(session.id)
    meta_path.write_text(json.dumps(session.__dict__), encoding="utf-8")


def begin(owner_id: str, filename: str, content_type: str,
          total_bytes: int) -> Session_:
    """Open an upload, refusing what would be refused at the end anyway.

    Checking the name and the declared size now means a friend on a phone finds
    out that a .aiff is not supported before spending twenty minutes uploading
    it, rather than after.
    """
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise UploadValidationError("Upload must be an audio or video file", 415)

    limit = settings.max_upload_bytes
    if is_image(filename):
        from app.services.storage import MAX_IMAGE_BYTES

        limit = MAX_IMAGE_BYTES
    if total_bytes <= 0:
        raise UploadValidationError("That file is empty", 400)
    if total_bytes > limit:
        raise UploadValidationError(
            f"That file is {total_bytes / 1e9:.1f} GB, over the "
            f"{limit / 1e9:.1f} GB limit for this server.", 413
        )

    sweep()
    session = Session_(
        id=str(uuid.uuid4()), owner_id=owner_id, filename=filename or "upload.bin",
        content_type=content_type or "application/octet-stream",
        total_bytes=total_bytes, received=0, next_index=0,
    )
    part_path, _ = _paths(session.id)
    part_path.touch()
    os.chmod(part_path, 0o640)
    _write(session)
    return session


def append(upload_id: str, owner_id: str, index: int, data: bytes) -> Session_:
    """Add one slice.

    Re-sending the slice just written is not an error: a client that lost the
    response and retried should not have to start over, and appending it twice
    would corrupt the file silently. The retry is recognised and ignored.
    """
    session = _read(upload_id)
    if session.owner_id != owner_id:
        # Someone else's upload. Reported as missing rather than forbidden so
        # that ids cannot be probed for existence.
        raise UploadValidationError("This upload has expired. Start it again.", 404)

    if index == session.next_index - 1:
        return session  # already have it
    if index != session.next_index:
        raise UploadValidationError(
            f"Chunk {index} arrived out of order; expected "
            f"{session.next_index}.", 409
        )
    if session.received + len(data) > session.total_bytes:
        raise UploadValidationError("Upload is larger than it said it was", 413)

    part_path, _ = _paths(upload_id)
    with part_path.open("ab") as handle:
        handle.write(data)
    session.received += len(data)
    session.next_index = index + 1
    _write(session)
    return session


def finish(upload_id: str, owner_id: str) -> tuple[str, int, Session_]:
    """Move the assembled file into the library and return its stored name."""
    session = _read(upload_id)
    if session.owner_id != owner_id:
        raise UploadValidationError("This upload has expired. Start it again.", 404)
    if not session.complete:
        missing = session.total_bytes - session.received
        raise UploadValidationError(
            f"The upload is not finished: {missing} bytes are missing.", 400
        )

    part_path, meta_path = _paths(upload_id)
    stored_name = make_stored_name(session.filename)
    destination = contained_path(
        settings.uploads_dir, settings.uploads_dir / stored_name
    )
    os.replace(part_path, destination)
    os.chmod(destination, 0o640)
    meta_path.unlink(missing_ok=True)
    return stored_name, session.received, session


def abort(upload_id: str, owner_id: str) -> None:
    """Give up on an upload and take its bytes back off the disk."""
    try:
        session = _read(upload_id)
    except UploadValidationError:
        return
    if session.owner_id != owner_id:
        return
    for path in _paths(upload_id):
        path.unlink(missing_ok=True)


def sweep(now: float | None = None) -> int:
    """Delete abandoned uploads.

    Without this, every closed tab leaves however much of an episode had
    arrived sitting in the staging directory forever, and the disk fills up
    with the beginnings of files nobody asked for again.
    """
    now = now if now is not None else time.time()
    removed = 0
    root = staging_dir()
    for meta_path in root.glob("*.json"):
        try:
            if now - meta_path.stat().st_mtime < STALE_SECONDS:
                continue
            part_path = meta_path.with_suffix(".part")
            part_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return removed
