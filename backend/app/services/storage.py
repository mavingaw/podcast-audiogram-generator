from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings

AUDIO_VIDEO_SUFFIXES = {
    ".aac",
    ".flac",
    ".m4a",
    ".mov",
    ".mp3",
    ".mp4",
    ".wav",
}
# Show artwork is what makes a clip recognisable in a feed, so images are a
# first-class upload rather than something pasted in as a data URL.
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_UPLOAD_SUFFIXES = AUDIO_VIDEO_SUFFIXES | IMAGE_SUFFIXES
ALLOWED_CONTENT_TYPES = {
    "audio/aac",
    "audio/flac",
    "audio/m4a",
    "audio/mp4",
    "audio/mpeg",
    "audio/wav",
    "audio/x-m4a",
    "audio/x-wav",
    "video/mp4",
    "video/quicktime",
    "image/png",
    "image/jpeg",
    "image/webp",
}

# Images are small; a 4K cover is a few megabytes, and anything larger is a
# mistake rather than a requirement.
MAX_IMAGE_BYTES = 24 * 1024 * 1024


def is_image(filename: str) -> bool:
    return Path(filename or "").suffix.lower() in IMAGE_SUFFIXES


class UploadValidationError(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def contained_path(root: Path, child: Path) -> Path:
    root_resolved = root.resolve()
    child_resolved = child.resolve()
    if root_resolved != child_resolved and root_resolved not in child_resolved.parents:
        raise ValueError("Path escapes storage root")
    return child_resolved


def make_stored_name(original_name: str) -> str:
    suffix = Path(original_name).suffix.lower()
    if len(suffix) > 16:
        suffix = ""
    return f"{uuid.uuid4()}{suffix}"


def validate_upload_request(upload: UploadFile) -> None:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise UploadValidationError("Upload must be an audio or video file", status_code=415)

    content_type = (upload.content_type or "").lower()
    if content_type and content_type != "application/octet-stream" and content_type not in ALLOWED_CONTENT_TYPES:
        raise UploadValidationError("Upload content type is not supported", status_code=415)


async def save_upload(upload: UploadFile) -> tuple[str, int]:
    validate_upload_request(upload)
    stored_name = make_stored_name(upload.filename or "upload.bin")
    destination = contained_path(settings.uploads_dir, settings.uploads_dir / stored_name)
    limit = MAX_IMAGE_BYTES if is_image(upload.filename or "") else settings.max_upload_bytes
    size = 0
    try:
        with destination.open("wb") as handle:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise UploadValidationError("Upload exceeds configured size limit", status_code=413)
                handle.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    os.chmod(destination, 0o640)
    return stored_name, size

