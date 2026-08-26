from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings


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


async def save_upload(upload: UploadFile) -> tuple[str, int]:
    stored_name = make_stored_name(upload.filename or "upload.bin")
    destination = contained_path(settings.uploads_dir, settings.uploads_dir / stored_name)
    size = 0
    with destination.open("wb") as handle:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            handle.write(chunk)
    os.chmod(destination, 0o640)
    return stored_name, size

