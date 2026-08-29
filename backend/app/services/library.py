"""Licensed sound library: import, catalogue, and query.

The library holds third-party music and UI effects that this installation is
licensed to *use* but not to *redistribute*. It therefore lives under
``settings.library_dir`` (a runtime volume), never in the repository and never
baked into the container image. ``import_pack`` copies files in from a pack
folder; :func:`sync_catalog` rebuilds the ``sound_assets`` rows from whatever is
on disk, so the database stays derived state that can be thrown away.

Every pack declares its licence and required attribution here, and every render
that uses a track writes those credits into its output directory.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import SoundAsset, SoundKind
from app.services.media import ffprobe_media
from app.services.song_index import index_by_stem, parse_song_index
from app.services.storage import contained_path

AUDIO_SUFFIXES = {".wav", ".mp3", ".ogg", ".m4a", ".flac", ".aac"}


@dataclass(frozen=True)
class PackLicense:
    """What a pack allows and what it demands in return."""

    slug: str
    name: str
    kind: SoundKind
    author: str
    license_name: str
    attribution: str
    redistributable: bool
    notes: str


PACKS: dict[str, PackLicense] = {
    "audio-asset-archive": PackLicense(
        slug="audio-asset-archive",
        name="Audio Asset Archive: Mega Music Multipack",
        kind=SoundKind.music,
        author="Aibioweapon",
        license_name="CC BY + royalty-free (Audio Asset Archive v1.0)",
        attribution="Audio by Aibioweapon",
        redistributable=False,
        notes=(
            "Commercial and non-commercial use in synced media including podcasts is "
            "granted worldwide and perpetually, and tracks may be remixed, trimmed, "
            "faded, or retitled. Crediting Aibioweapon is mandatory. Selling or "
            "redistributing the files standalone or as part of another library is not."
        ),
    ),
    "jdsherbert-ultimate-ui-sfx": PackLicense(
        slug="jdsherbert-ultimate-ui-sfx",
        name="JDSherbert - Ultimate UI SFX Pack (FREE)",
        kind=SoundKind.sfx,
        author="JDSherbert",
        license_name="JDSherbert product licence (2023)",
        attribution="UI sounds by JDSherbert",
        redistributable=False,
        notes=(
            "Licensed for commercial and non-commercial projects with credit to "
            "JDSherbert. The licence forbids modifying or redistributing the files, "
            "so they are copied in verbatim and never transcoded."
        ),
    ),
}

# The effects the interface actually plays, mapped to the pack's file stems.
SFX_ROLES: dict[str, str] = {
    "select": "Select - 1",
    "confirm": "Select - 2",
    "cursor": "Cursor - 1",
    "cancel": "Cancel - 1",
    "error": "Error - 1",
    "open": "Popup Open - 1",
    "close": "Popup Close - 1",
    "swipe": "Swipe - 1",
}


class LibraryImportError(RuntimeError):
    pass


# Tracks with a separate intro ship as "<stem>_Intro" / "<stem>_Loop" pairs,
# which the song index lists under the bare stem.
_SECTION_SUFFIXES = ("_intro", "_loop")


def _is_sidecar(path: Path) -> bool:
    """True for macOS resource forks and .DS_Store left in the source packs."""
    return path.name.startswith(".")


def _clean_filename(name: str) -> str:
    stem, _, suffix = name.rpartition(".")
    return f"{stem.strip()}.{suffix}" if stem else name.strip()


def split_section(stem: str) -> tuple[str, str | None]:
    """Return the index stem and the section name for an intro/loop pair file."""
    lowered = stem.lower()
    for suffix in _SECTION_SUFFIXES:
        if lowered.endswith(suffix):
            return stem[: -len(suffix)], suffix.lstrip("_")
    return stem, None


def _lookup(records: dict, stem: str) -> object | None:
    base, _ = split_section(stem)
    return records.get(stem.lower()) or records.get(base.lower())


def pack_dir(pack: PackLicense) -> Path:
    return settings.library_dir / pack.kind.value / pack.slug


def sound_path(asset: SoundAsset) -> Path:
    return contained_path(settings.library_dir, settings.library_dir / asset.relative_path)


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------


def import_music_pack(source_dirs: list[Path], song_index: Path | None) -> dict:
    """Copy Audio Asset Archive WAVs into the library and write its manifest."""
    pack = PACKS["audio-asset-archive"]
    destination = pack_dir(pack)
    destination.mkdir(parents=True, exist_ok=True)

    records = index_by_stem(parse_song_index(song_index)) if song_index else {}
    copied, skipped, unmatched = 0, 0, []

    for source_dir in source_dirs:
        if not source_dir.exists():
            raise LibraryImportError(f"Source folder not found: {source_dir}")
        for source in sorted(source_dir.rglob("*")):
            if source.suffix.lower() not in AUDIO_SUFFIXES or _is_sidecar(source):
                continue
            target = destination / _clean_filename(source.name)
            if target.exists() and target.stat().st_size == source.stat().st_size:
                skipped += 1
            else:
                shutil.copy2(source, target)
                copied += 1
            if song_index and _lookup(records, target.stem) is None:
                unmatched.append(target.name)

    _write_manifest(pack, destination, records)
    return {
        "pack": pack.slug,
        "copied": copied,
        "already_present": skipped,
        "indexed_tracks": len(records),
        "unmatched_files": unmatched,
    }


def import_sfx_pack(source_dir: Path, preferred_format: str = "ogg") -> dict:
    """Copy the UI effect pack in verbatim.

    Its licence forbids modification, so we pick one of the formats the pack
    already ships rather than transcoding anything.
    """
    pack = PACKS["jdsherbert-ultimate-ui-sfx"]
    if not source_dir.exists():
        raise LibraryImportError(f"Source folder not found: {source_dir}")

    destination = pack_dir(pack)
    destination.mkdir(parents=True, exist_ok=True)

    candidates = [
        path
        for path in source_dir.rglob(f"*.{preferred_format}")
        if "stereo" in str(path).lower()
    ]
    if not candidates:
        candidates = list(source_dir.rglob(f"*.{preferred_format}"))
    if not candidates:
        raise LibraryImportError(f"No .{preferred_format} files under {source_dir}")

    copied, roles = 0, {}
    for role, stem_suffix in SFX_ROLES.items():
        match = next((path for path in candidates if path.stem.endswith(stem_suffix)), None)
        if match is None:
            continue
        target = destination / f"{role}{match.suffix}"
        shutil.copy2(match, target)
        roles[role] = target.name
        copied += 1

    _write_manifest(pack, destination, {}, extra={"roles": roles})
    return {"pack": pack.slug, "copied": copied, "roles": roles}


def _write_manifest(
    pack: PackLicense,
    destination: Path,
    records: dict,
    extra: dict | None = None,
) -> None:
    manifest = {
        "pack": pack.slug,
        "name": pack.name,
        "author": pack.author,
        "license": pack.license_name,
        "attribution": pack.attribution,
        "redistributable": pack.redistributable,
        "notes": pack.notes,
        "tracks": {
            record.file_stem: {
                "number": record.number,
                "title": record.title,
                "author": record.author,
                "genre": record.genre,
                "duration_seconds": record.duration_seconds,
                "seamless_loop": record.seamless_loop,
                "has_intro": record.has_intro,
                "tags": record.tags,
            }
            for record in records.values()
        },
    }
    if extra:
        manifest.update(extra)
    (destination / "pack.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------


def sync_catalog(db: Session, probe_durations: bool = False) -> dict:
    """Rebuild ``sound_assets`` from the files present under the library root."""
    seen: set[str] = set()
    added, updated = 0, 0

    for pack in PACKS.values():
        directory = pack_dir(pack)
        if not directory.exists():
            continue
        manifest = _read_manifest(directory)
        tracks = manifest.get("tracks", {})

        for path in sorted(directory.iterdir()):
            if path.suffix.lower() not in AUDIO_SUFFIXES or _is_sidecar(path):
                continue
            relative = path.relative_to(settings.library_dir).as_posix()
            seen.add(relative)
            base, section = split_section(path.stem)
            meta = tracks.get(path.stem) or tracks.get(base) or {}
            # An intro plays once ahead of its loop, so only the loop half is
            # safe to repeat under a long clip.
            loopable = bool(meta.get("seamless_loop", True)) and section != "intro"
            duration = meta.get("duration_seconds") if section is None else None
            if duration is None and probe_durations:
                duration = ffprobe_media(path)[0]

            asset = db.scalar(select(SoundAsset).where(SoundAsset.relative_path == relative))
            if asset is None:
                asset = SoundAsset(relative_path=relative)
                db.add(asset)
                added += 1
            else:
                updated += 1

            title = meta.get("title") or _title_from_stem(base)
            asset.kind = pack.kind
            asset.pack = pack.slug
            asset.title = f"{title} ({section.title()})" if section else title
            asset.author = meta.get("author") or pack.author
            asset.attribution = pack.attribution
            asset.license_name = pack.license_name
            asset.redistributable = pack.redistributable
            asset.genre = meta.get("genre", "")
            asset.tags_json = json.dumps(meta.get("tags", []))
            asset.duration_seconds = duration
            asset.seamless_loop = loopable
            asset.size_bytes = path.stat().st_size

    removed = 0
    for asset in db.scalars(select(SoundAsset)).all():
        if asset.relative_path not in seen:
            db.delete(asset)
            removed += 1

    db.commit()
    return {"added": added, "updated": updated, "removed": removed, "total": len(seen)}


def _read_manifest(directory: Path) -> dict:
    manifest_path = directory / "pack.json"
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _title_from_stem(stem: str) -> str:
    return stem.replace("_", " ").replace("-", " ").strip().title()


def serialize_sound(asset: SoundAsset) -> dict:
    return {
        "id": asset.id,
        "kind": asset.kind.value,
        "pack": asset.pack,
        "title": asset.title,
        "author": asset.author,
        "attribution": asset.attribution,
        "license": asset.license_name,
        "genre": asset.genre,
        "tags": json.loads(asset.tags_json or "[]"),
        "duration_seconds": asset.duration_seconds,
        "seamless_loop": asset.seamless_loop,
        "size_bytes": asset.size_bytes,
        "preview_url": f"/api/library/sounds/{asset.id}/file",
    }


def query_sounds(
    db: Session,
    kind: str | None = None,
    genre: str | None = None,
    search: str | None = None,
    limit: int = 200,
) -> list[SoundAsset]:
    statement = select(SoundAsset)
    if kind:
        statement = statement.where(SoundAsset.kind == SoundKind(kind))
    if genre:
        statement = statement.where(SoundAsset.genre == genre)
    if search:
        needle = f"%{search.strip().lower()}%"
        statement = statement.where(
            SoundAsset.title.ilike(needle)
            | SoundAsset.tags_json.ilike(needle)
            | SoundAsset.genre.ilike(needle)
        )
    statement = statement.order_by(SoundAsset.genre.asc(), SoundAsset.title.asc()).limit(limit)
    return list(db.scalars(statement).all())


def genres(db: Session) -> list[str]:
    values = db.scalars(
        select(SoundAsset.genre).where(SoundAsset.kind == SoundKind.music).distinct()
    ).all()
    return sorted(value for value in values if value)


def credits_for(db: Session, sound_ids: list[str]) -> list[dict]:
    """Attribution lines for the given tracks, deduplicated by pack and author."""
    if not sound_ids:
        return []
    assets = db.scalars(select(SoundAsset).where(SoundAsset.id.in_(sound_ids))).all()
    lines: dict[tuple[str, str], dict] = {}
    for asset in assets:
        key = (asset.pack, asset.author)
        entry = lines.setdefault(
            key,
            {
                "pack": PACKS[asset.pack].name if asset.pack in PACKS else asset.pack,
                "author": asset.author,
                "license": asset.license_name,
                "attribution": asset.attribution,
                "tracks": [],
            },
        )
        entry["tracks"].append(asset.title)
    for entry in lines.values():
        entry["tracks"].sort()
    return list(lines.values())


def installed_packs(db: Session) -> list[dict]:
    summary = []
    for pack in PACKS.values():
        count = len(
            db.scalars(select(SoundAsset.id).where(SoundAsset.pack == pack.slug)).all()
        )
        summary.append(
            {
                "slug": pack.slug,
                "name": pack.name,
                "kind": pack.kind.value,
                "author": pack.author,
                "license": pack.license_name,
                "attribution": pack.attribution,
                "redistributable": pack.redistributable,
                "notes": pack.notes,
                "installed": count > 0,
                "sound_count": count,
            }
        )
    return summary
