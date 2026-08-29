"""Pull thousands of licensed sounds into the library in one go.

Clicking through a stock site one file at a time is not a way to build a
library. Two sources hand over a catalogue at once, with per-file licences:

  Free Music Archive dataset — the whole FMA catalogue as one download, with
  a licence for every track. `medium` is 25,000 tracks (24 GB); `large` is
  106,000 (100 GB). Clips are 30 seconds, which is the length of a social
  clip. No account needed.

      python -m app.cli.fetch_library fma --size medium

  Freesound — CC0 sound effects by search term, hundreds per query, through
  their API. Needs a free key from https://freesound.org/apiv2/apply/.

      python -m app.cli.fetch_library freesound --key KEY \\
          --query whoosh --query "riser" --query stinger --per-query 200

Both write a pack folder under the library with a pack.json that carries each
track's own licence and attribution, and then rebuild the catalogue, so the
tracks appear in the music and effects pickers and every export that uses
one writes the right credit.

Only licences that allow commercial use are kept. FMA in particular is full
of CC BY-NC, and a clip that earns money for a show must not carry one.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

FMA_BASE = "https://os.unil.cloud.switch.ch/fma/"
FMA_SIZES = {"small": 8_000, "medium": 25_000, "large": 106_574}

# Licence names as FMA writes them, mapped to a short label and whether an
# export using the track must carry a credit. Anything not in this table is
# left out of the library: the common absentees are the NonCommercial and
# NoDerivatives variants, and "All Rights Reserved" tracks the archive hosts
# on a listening-only basis.
FMA_ALLOWED = {
    "Creative Commons Zero (CC0)": ("CC0 1.0", False),
    "CC0 1.0 Universal": ("CC0 1.0", False),
    "Public Domain Mark": ("Public domain", False),
    "Attribution": ("CC BY 4.0", True),
    "Attribution 4.0": ("CC BY 4.0", True),
    "Attribution 3.0": ("CC BY 3.0", True),
    "Attribution-ShareAlike": ("CC BY-SA 4.0", True),
    "Attribution-ShareAlike 3.0": ("CC BY-SA 3.0", True),
}

FREESOUND_API = "https://freesound.org/apiv2/"
# Freesound's own name for CC0. Filtering server-side means nothing else is
# even listed, rather than downloaded and discarded.
FREESOUND_LICENSE_FILTER = 'license:"Creative Commons 0"'


class FetchError(RuntimeError):
    pass


def slugify(text: str, limit: int = 60) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text or "").strip("-").lower()
    return text[:limit] or "track"


def fma_licence(raw: str) -> tuple[str, bool] | None:
    """Normalise an FMA licence string, or None if it is not one we keep."""
    text = (raw or "").strip().lower()
    if not text:
        return None
    # Refused outright before any matching. "Attribution-NonCommercial"
    # starts with "Attribution", and a prefix match let it through as CC BY —
    # the one mistake this whole filter exists to prevent. A test caught it.
    if any(word in text for word in ("noncommercial", "non-commercial", "noderiv", "no derivative", "-nc", "-nd")):
        return None
    # Longest names first, so "Attribution-ShareAlike 3.0" is not read as
    # plain "Attribution".
    for key in sorted(FMA_ALLOWED, key=len, reverse=True):
        if text.startswith(key.lower()):
            return FMA_ALLOWED[key]
    return None


def read_fma_tracks(metadata_zip: Path) -> dict[int, dict]:
    """Track id -> {title, author, genre, duration, licence} from tracks.csv.

    The CSV has a three-row header (table, column, blank); the columns needed
    are addressed by their (table, column) pair rather than by position,
    because the position has changed between dataset versions.
    """
    with zipfile.ZipFile(metadata_zip) as archive:
        # By basename, exactly. The archive also holds raw_tracks.csv, which
        # ends in "tracks.csv" too and has a completely different header; a
        # suffix match picked it and failed on the first column lookup.
        member = next(
            name for name in archive.namelist()
            if name.rsplit("/", 1)[-1] == "tracks.csv"
        )
        with archive.open(member) as handle:
            text = io.TextIOWrapper(handle, encoding="utf-8")
            reader = csv.reader(text)
            tables = next(reader)
            columns = next(reader)
            next(reader)  # the blank index row
            index = {(t, c): i for i, (t, c) in enumerate(zip(tables, columns))}
            wanted = {
                "title": index[("track", "title")],
                "license": index[("track", "license")],
                "duration": index[("track", "duration")],
                "genre": index[("track", "genre_top")],
                "artist": index[("artist", "name")],
                "subset": index[("set", "subset")],
            }
            tracks: dict[int, dict] = {}
            for row in reader:
                if not row or not row[0].strip().isdigit():
                    continue
                licence = fma_licence(row[wanted["license"]])
                if licence is None:
                    continue
                label, needs_credit = licence
                tracks[int(row[0])] = {
                    "title": row[wanted["title"]].strip() or f"Track {row[0]}",
                    "author": row[wanted["artist"]].strip() or "Unknown artist",
                    "genre": row[wanted["genre"]].strip(),
                    "duration_seconds": _number(row[wanted["duration"]]),
                    "license": label,
                    "needs_credit": needs_credit,
                    "subset": row[wanted["subset"]].strip(),
                }
            return tracks


def _number(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def download(url: str, target: Path, expected: int | None = None, log=print) -> Path:
    """Stream a large file to disk, resuming if part of it is already there.

    A 24 GB download over a home connection will be interrupted at least
    once; starting over each time would never finish.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    have = partial.stat().st_size if partial.exists() else 0
    if target.exists() and (expected is None or target.stat().st_size == expected):
        log(f"already have {target.name}")
        return target

    request = urllib.request.Request(url, headers={"User-Agent": "Kinder/1.0"})
    if have:
        request.add_header("Range", f"bytes={have}-")
    with urllib.request.urlopen(request, timeout=120) as response:
        if have and response.status != 206:
            # The host would not resume; start again.
            have = 0
        total = have + int(response.headers.get("Content-Length") or 0)
        mode = "ab" if have else "wb"
        done = have
        last = time.monotonic()
        with partial.open(mode) as handle:
            while True:
                chunk = response.read(1 << 22)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                if time.monotonic() - last > 15:
                    pct = f" ({done * 100 // total}%)" if total else ""
                    log(f"  {target.name}: {done / 1e9:.2f} GB{pct}")
                    last = time.monotonic()
    partial.replace(target)
    return target


def fetch_fma(
    library_dir: Path,
    size: str = "medium",
    limit: int | None = None,
    genres: set[str] | None = None,
    log=print,
) -> dict:
    """Download an FMA subset and keep the commercially usable tracks."""
    if size not in FMA_SIZES:
        raise FetchError(f"size must be one of {', '.join(FMA_SIZES)}")

    downloads = library_dir / "_downloads"
    metadata = download(FMA_BASE + "fma_metadata.zip", downloads / "fma_metadata.zip", log=log)
    tracks = read_fma_tracks(metadata)
    log(f"{len(tracks)} commercially usable tracks in the FMA catalogue")

    archive_path = download(FMA_BASE + f"fma_{size}.zip", downloads / f"fma_{size}.zip", log=log)

    pack = library_dir / "music" / f"fma-{size}"
    pack.mkdir(parents=True, exist_ok=True)
    manifest_tracks: dict[str, dict] = {}
    kept = skipped = 0

    with zipfile.ZipFile(archive_path) as archive:
        members = {
            Path(name).stem: name
            for name in archive.namelist()
            if name.lower().endswith(".mp3")
        }
        for stem, member in sorted(members.items()):
            if not stem.isdigit():
                continue
            meta = tracks.get(int(stem))
            if meta is None:
                skipped += 1
                continue
            if genres and meta["genre"] not in genres:
                skipped += 1
                continue
            target = pack / f"{stem}-{slugify(meta['title'])}.mp3"
            if not target.exists():
                with archive.open(member) as source, target.open("wb") as sink:
                    sink.write(source.read())
            credit = (
                f"\"{meta['title']}\" by {meta['author']} ({meta['license']}), via Free Music Archive"
                if meta["needs_credit"] else ""
            )
            manifest_tracks[target.stem] = {
                "title": meta["title"],
                "author": meta["author"],
                "genre": meta["genre"],
                "duration_seconds": meta["duration_seconds"],
                "license": meta["license"],
                "attribution": credit,
                "url": f"https://freemusicarchive.org/track/{stem}",
                "seamless_loop": False,
                "tags": [g for g in [meta["genre"]] if g],
            }
            kept += 1
            if limit and kept >= limit:
                break

    write_manifest(
        pack,
        name=f"Free Music Archive ({size})",
        author="Various artists",
        license_name="Per track: CC0, CC BY, or CC BY-SA",
        attribution="Music via Free Music Archive; see individual tracks",
        notes=(
            "The FMA dataset (Defferrard et al., ISMIR 2017). Only tracks under "
            "CC0, CC BY or CC BY-SA are kept; NonCommercial and NoDerivatives "
            "variants are left out. Files are 30-second clips."
        ),
        tracks=manifest_tracks,
    )
    return {"pack": pack.name, "kept": kept, "skipped_unlicensed": skipped}


def freesound_request(path: str, key: str, **params) -> dict:
    query = urllib.parse.urlencode({**params, "token": key})
    request = urllib.request.Request(
        FREESOUND_API + path + "?" + query, headers={"User-Agent": "Kinder/1.0"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_freesound(
    library_dir: Path,
    key: str,
    queries: list[str],
    per_query: int = 100,
    min_seconds: float = 0.1,
    max_seconds: float = 30.0,
    log=print,
) -> dict:
    """Pull CC0 effects for each search term into one Freesound pack.

    The high-quality MP3 preview is what is downloaded: the original file
    needs an OAuth dance per user, and a 128 kbps effect under a voice is
    indistinguishable from the WAV. CC0 only, so no export ever needs a
    Freesound credit line — though the manifest records the author anyway.
    """
    if not key:
        raise FetchError("A Freesound API key is required (https://freesound.org/apiv2/apply/)")
    pack = library_dir / "sfx" / "freesound"
    pack.mkdir(parents=True, exist_ok=True)
    manifest_path = pack / "pack.json"
    existing = {}
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8")).get("tracks", {})
        except json.JSONDecodeError:
            existing = {}
    tracks: dict[str, dict] = dict(existing)
    fetched = 0

    for query in queries:
        got, page = 0, 1
        while got < per_query:
            payload = freesound_request(
                "search/text/", key,
                query=query,
                filter=f"{FREESOUND_LICENSE_FILTER} duration:[{min_seconds} TO {max_seconds}]",
                fields="id,name,username,license,previews,tags,duration,url",
                page_size=min(150, per_query - got),
                page=page,
                sort="rating_desc",
            )
            results = payload.get("results") or []
            if not results:
                break
            for item in results:
                stem = f"{item['id']}-{slugify(item.get('name', ''))}"
                target = pack / f"{stem}.mp3"
                if not target.exists():
                    preview = (item.get("previews") or {}).get("preview-hq-mp3")
                    if not preview:
                        continue
                    request = urllib.request.Request(preview, headers={"User-Agent": "Kinder/1.0"})
                    with urllib.request.urlopen(request, timeout=60) as response:
                        target.write_bytes(response.read())
                    fetched += 1
                tracks[stem] = {
                    "title": item.get("name") or stem,
                    "author": item.get("username") or "Unknown",
                    "genre": query,
                    "duration_seconds": item.get("duration"),
                    "license": "CC0 1.0",
                    "attribution": "",
                    "url": item.get("url") or f"https://freesound.org/s/{item['id']}/",
                    "seamless_loop": False,
                    "tags": [query] + list(item.get("tags") or [])[:8],
                }
                got += 1
                if got >= per_query:
                    break
            if not payload.get("next"):
                break
            page += 1
            # Well inside the 60 requests a minute the API allows.
            time.sleep(1.1)
        log(f"{query}: {got} effects")

    write_manifest(
        pack,
        name="Freesound (CC0)",
        author="Various",
        license_name="CC0 1.0",
        attribution="",
        notes="CC0 effects fetched from freesound.org by search term. No credit is required.",
        tracks=tracks,
    )
    return {"pack": pack.name, "fetched": fetched, "total": len(tracks)}


def write_manifest(pack: Path, *, name: str, author: str, license_name: str,
                   attribution: str, notes: str, tracks: dict) -> None:
    manifest = {
        "pack": pack.name,
        "name": name,
        "author": author,
        "license": license_name,
        "attribution": attribution,
        "redistributable": False,
        "notes": notes,
        "tracks": tracks,
    }
    (pack / "pack.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="source", required=True)

    fma = sub.add_parser("fma", help="Free Music Archive dataset")
    fma.add_argument("--size", default="medium", choices=sorted(FMA_SIZES))
    fma.add_argument("--limit", type=int, help="Stop after this many tracks")
    fma.add_argument("--genre", action="append", default=[],
                     help="Keep only these top-level genres (repeatable)")

    fs = sub.add_parser("freesound", help="CC0 effects from freesound.org")
    fs.add_argument("--key", required=True, help="Freesound API key")
    fs.add_argument("--query", action="append", required=True, help="Search term (repeatable)")
    fs.add_argument("--per-query", type=int, default=100)
    fs.add_argument("--max-seconds", type=float, default=30.0)

    parser.add_argument("--no-sync", action="store_true", help="Skip rebuilding the catalogue")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from app.core.config import ensure_directories, settings

    ensure_directories()
    report: dict = {"library_dir": str(settings.library_dir)}
    try:
        if args.source == "fma":
            report["fma"] = fetch_fma(
                settings.library_dir, args.size, args.limit, set(args.genre) or None
            )
        elif args.source == "freesound":
            report["freesound"] = fetch_freesound(
                settings.library_dir, args.key, args.query, args.per_query,
                max_seconds=args.max_seconds,
            )
    except FetchError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if not args.no_sync:
        from app.db.init_db import init_db
        from app.db.session import SessionLocal
        from app.services.library import sync_catalog

        init_db()
        with SessionLocal() as db:
            report["catalog"] = sync_catalog(db)

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
