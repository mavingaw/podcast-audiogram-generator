"""Import licensed sound packs into this installation's library.

    python -m app.cli.import_library \
        --music-dir "C:/Users/you/Downloads/Audio_Asset_Archive_WAVs_1-100" \
        --song-index "C:/Users/you/Downloads/Audio_Asset_Archive_SONG_INDEX.rtf" \
        --sfx-dir "C:/Users/you/Downloads/JDSherbert - Ultimate UI SFX Pack (FREE)"

Every argument is optional; ``--sync-only`` re-reads whatever is already on
disk. Files are copied into ``PAS_LIBRARY_DIR`` (default
``runtime/data/library``), which is a runtime volume, not repository content —
the packs permit use, not redistribution.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.core.config import ensure_directories, settings
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.services.library import (
    LibraryImportError,
    import_music_pack,
    import_sfx_pack,
    sync_catalog,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--music-dir",
        action="append",
        default=[],
        type=Path,
        help="Audio Asset Archive WAV folder; repeat for each volume.",
    )
    parser.add_argument(
        "--song-index",
        type=Path,
        help="Audio_Asset_Archive_SONG_INDEX.rtf, for titles, genres, and tags.",
    )
    parser.add_argument("--sfx-dir", type=Path, help="JDSherbert UI SFX pack folder.")
    parser.add_argument(
        "--sfx-format",
        default="ogg",
        choices=["ogg", "mp3", "m4a", "wav"],
        help="Which of the shipped formats to install (files are never transcoded).",
    )
    parser.add_argument(
        "--probe-durations",
        action="store_true",
        help="Run ffprobe on tracks the song index does not cover. Slow.",
    )
    parser.add_argument(
        "--sync-only",
        action="store_true",
        help="Skip copying and just rebuild the catalogue from disk.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ensure_directories()
    init_db()

    report: dict[str, object] = {"library_dir": str(settings.library_dir)}

    try:
        if args.music_dir and not args.sync_only:
            report["music"] = import_music_pack(args.music_dir, args.song_index)
        if args.sfx_dir and not args.sync_only:
            report["sfx"] = import_sfx_pack(args.sfx_dir, args.sfx_format)
    except LibraryImportError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        report["catalog"] = sync_catalog(db, probe_durations=args.probe_durations)

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
