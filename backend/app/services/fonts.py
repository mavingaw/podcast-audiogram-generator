"""Fonts people bring themselves.

The four bundled faces are safe and licensed, but a show has its own brand
font. Upload a TTF/OTF once and it appears in every font picker, renders
into titles (drawtext takes the file directly) and captions (libass finds
it by family name in the shared fonts directory).

Files live in one directory under uploads; who owns which font is a small
registry in the settings table. The family name is read from the font's own
name table, because that is the name libass will match against — a filename
is not good enough.
"""

from __future__ import annotations

import json
import logging
import secrets
import struct
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import AppSetting

log = logging.getLogger(__name__)

MAX_BYTES = 5 * 1024 * 1024
# sfnt version tags: TrueType, Apple 'true', OpenType with CFF.
MAGIC = (b"\x00\x01\x00\x00", b"true", b"OTTO")


class FontError(RuntimeError):
    pass


def fonts_dir() -> Path:
    from app.core.config import settings

    directory = settings.uploads_dir / "fonts"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _key(user_id: str) -> str:
    return f"fonts:{user_id}"


def list_fonts(db: Session, user_id: str) -> list[dict]:
    row = db.get(AppSetting, _key(user_id))
    if not row or not row.value:
        return []
    try:
        return json.loads(row.value)
    except json.JSONDecodeError:
        return []


def _store(db: Session, user_id: str, entries: list[dict]) -> None:
    row = db.get(AppSetting, _key(user_id)) or AppSetting(key=_key(user_id), value="")
    row.value = json.dumps(entries)
    db.merge(row)
    db.commit()


def save_font(db: Session, user_id: str, filename: str, data: bytes) -> dict:
    if len(data) > MAX_BYTES:
        raise FontError("Fonts are small files; this one is over 5 MB")
    if not data[:4] in MAGIC:
        raise FontError("That is not a TTF or OTF font file")
    suffix = ".otf" if data[:4] == b"OTTO" else ".ttf"
    font_id = "uf-" + secrets.token_hex(4)
    stem = Path(filename).stem.strip() or "Font"
    family = family_from_bytes(data) or stem
    # The family name is written into a comma-separated ASS style line and
    # into fontconfig lookups: strip the characters that would let a crafted
    # font shift fields or inject lines, and keep it a sane length.
    family = "".join(c for c in family if c.isprintable() and c not in ",;{}\\").strip()[:80] or stem[:80]
    entries = list_fonts(db, user_id)
    if any(e["family"].lower() == family.lower() for e in entries):
        raise FontError(f"A font named {family} is already in your list")
    target = fonts_dir() / f"{font_id}{suffix}"
    target.write_bytes(data)
    entries.append({"id": font_id, "family": family, "file": target.name})
    _store(db, user_id, entries)
    return {"id": font_id, "family": family}


def delete_font(db: Session, user_id: str, font_id: str) -> bool:
    entries = list_fonts(db, user_id)
    keep = [e for e in entries if e["id"] != font_id]
    if len(keep) == len(entries):
        return False
    gone = next(e for e in entries if e["id"] == font_id)
    (fonts_dir() / gone["file"]).unlink(missing_ok=True)
    _store(db, user_id, keep)
    return True


def entry_for(db: Session, user_id: str, font_id: str) -> dict | None:
    return next((e for e in list_fonts(db, user_id) if e["id"] == font_id), None)


def register_scene_fonts(db: Session, user_id: str, scene: dict) -> None:
    """Make the scene's custom fonts resolvable by the renderer.

    scene.CUSTOM_FONTS maps a font id to (family, file path); ids are unique
    per upload, so entries from parallel renders never collide.
    """
    from app.services import scene as scene_module

    for field in ("font", "captionFont"):
        font_id = str(scene.get(field) or "")
        if not font_id.startswith("uf-"):
            continue
        entry = entry_for(db, user_id, font_id)
        if not entry:
            continue
        path = fonts_dir() / entry["file"]
        if path.exists():
            scene_module.CUSTOM_FONTS[font_id] = (entry["family"], str(path))


def family_from_bytes(data: bytes) -> str | None:
    """The family name (nameID 1) out of the sfnt name table.

    A wrong name here means libass silently falls back to some other face,
    so unparseable is returned as None and the caller uses the filename.
    """
    try:
        num_tables = struct.unpack(">H", data[4:6])[0]
        name_offset = None
        for i in range(num_tables):
            rec = data[12 + i * 16: 28 + i * 16]
            if rec[:4] == b"name":
                name_offset = struct.unpack(">I", rec[8:12])[0]
                break
        if name_offset is None:
            return None
        count, string_offset = struct.unpack(">HH", data[name_offset + 2: name_offset + 6])
        storage = name_offset + string_offset
        best: str | None = None
        for i in range(count):
            rec = data[name_offset + 6 + i * 12: name_offset + 18 + i * 12]
            platform, encoding, _lang, name_id, length, offset = struct.unpack(">6H", rec)
            if name_id != 1:
                continue
            raw = data[storage + offset: storage + offset + length]
            if platform == 3:
                best = raw.decode("utf-16-be", errors="ignore").strip()
                break
            if platform == 1 and best is None:
                best = raw.decode("latin-1", errors="ignore").strip()
        return best or None
    except Exception:
        return None
