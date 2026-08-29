"""Parser for the Audio Asset Archive song index.

The archive ships its catalogue as an RTF/PDF document rather than machine
readable metadata, so the importer reads the RTF and recovers the per-track
records. Entries look like this once the RTF markup is stripped::

    01 - AFRICA

    001  -  Bantu Beach by Aibioweapon
    Africa_Bantu_Beach
    Duration: 0:32
    < Africa, Bantu, Congo, Bells, Tropical, Uplifting >

Some titles carry a second credit (``by J. S. Bach transcribed by
Aibioweapon``) and some tracks are one-shots rather than seamless loops, which
the duration line flags in free text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_RTF_GROUPS = (
    "fonttbl",
    "colortbl",
    "expandedcolortbl",
    "stylesheet",
    "listtable",
    "listoverridetable",
    "rsidtbl",
    "generator",
    "info",
)

_GENRE_RE = re.compile(r"^\d{2}\s*-\s*([A-Z0-9][A-Z0-9 '&/-]+)$")
_ENTRY_RE = re.compile(r"^(\d{1,3})\s*-\s*(.+)$")
# A handful of index entries omit the trailing "by <author>" credit.
_DEFAULT_AUTHOR = "Aibioweapon"
_DURATION_RE = re.compile(r"^Duration:\s*(\d+):(\d{2})(.*)$", re.IGNORECASE)
_TAGS_RE = re.compile(r"^<\s*(.*?)\s*>$")
_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_'.-]*$")


@dataclass
class SongRecord:
    number: int
    title: str
    author: str
    file_stem: str
    genre: str
    duration_seconds: float | None = None
    seamless_loop: bool = True
    has_intro: bool = False
    tags: list[str] = field(default_factory=list)


def rtf_to_text(path: Path) -> str:
    """Flatten an RTF document to plain text.

    Deliberately minimal: the index uses one font, no tables, and no embedded
    objects, so stripping control words and unescaping hex literals is enough.
    """
    raw = path.read_bytes().decode("cp1252", "ignore")
    for group in _RTF_GROUPS:
        raw = re.sub(r"\{\\\*?\\" + group + r".*?\}\s*(?=\\|\{)", "", raw, flags=re.S)
    raw = re.sub(
        r"\\'([0-9a-fA-F]{2})",
        lambda m: bytes([int(m.group(1), 16)]).decode("cp1252", "ignore"),
        raw,
    )
    # \uNNNN carries the real character and is followed by an ASCII fallback
    # glyph for readers that cannot render it; keep the former, drop the latter.
    raw = re.sub(r"\\u(-?\d+)\s*\??", lambda m: chr(int(m.group(1)) % 65536), raw)
    raw = re.sub(r"\\(par|line)\b", "\n", raw)
    raw = re.sub(r"\\tab\b", "\t", raw)
    raw = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", raw)
    return raw.replace("{", "").replace("}", "")


def _clean_lines(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        line = line.replace("\\", " ").replace("\u00a0", " ")
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return lines


def parse_song_index(path: Path) -> list[SongRecord]:
    """Return every track record found in the archive's song index."""
    text = rtf_to_text(path) if path.suffix.lower() == ".rtf" else path.read_text("utf-8", "ignore")
    lines = _clean_lines(text)

    records: list[SongRecord] = []
    genre = ""
    current: SongRecord | None = None

    for line in lines:
        genre_match = _GENRE_RE.match(line)
        if genre_match and len(line) < 48:
            genre = genre_match.group(1).strip().title()
            continue

        entry_match = _ENTRY_RE.match(line)
        if entry_match:
            number, remainder = entry_match.groups()
            title, author = _split_credit(remainder)
            current = SongRecord(
                number=int(number),
                title=title.strip(),
                author=author,
                file_stem="",
                genre=genre,
            )
            records.append(current)
            continue

        if current is None:
            continue

        if not current.file_stem and _FILENAME_RE.match(line) and "_" in line:
            current.file_stem = line
            continue

        duration_match = _DURATION_RE.match(line)
        if duration_match:
            minutes, seconds, trailer = duration_match.groups()
            current.duration_seconds = int(minutes) * 60 + int(seconds)
            # Seamless loop is the archive's default. The two exceptions are
            # labelled "( Does Not Repeat )" and "( Intro + Loop )"; only the
            # former is unsafe to loop as a music bed.
            current.seamless_loop = "does not repeat" not in trailer.lower()
            current.has_intro = "intro" in trailer.lower()
            continue

        tags_match = _TAGS_RE.match(line)
        if tags_match:
            current.tags = [tag.strip() for tag in tags_match.group(1).split(",") if tag.strip()]
            current = None

    return [record for record in records if record.file_stem]


def _split_credit(remainder: str) -> tuple[str, str]:
    """Split an entry's ``<title> by <author>`` tail into title and author.

    Public-domain arrangements credit two people (``<title> by <composer>
    transcribed by <arranger>``). The licence requires crediting the archive's
    own author, so the transcriber becomes the author and the composer is
    folded into the title. A few entries carry no credit at all.
    """
    if " transcribed by " in remainder:
        head, transcriber = remainder.split(" transcribed by ", 1)
        title, _, composer = head.rpartition(" by ")
        if title:
            return f"{title.strip()} ({composer.strip()})", transcriber.strip()
        return head.strip(), transcriber.strip()

    title, separator, author = remainder.rpartition(" by ")
    if not separator:
        return remainder.strip(), _DEFAULT_AUTHOR
    return title.strip(), author.strip()


def index_by_stem(records: list[SongRecord]) -> dict[str, SongRecord]:
    return {record.file_stem.lower(): record for record in records}
