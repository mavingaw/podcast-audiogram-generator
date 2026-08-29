"""Recognising a render that has already been done.

Queueing the same clip twice is easy and expensive. A double-clicked Export, a
retry after a timeout that had already succeeded, a batch that overlaps a
previous batch — each costs a GPU minute to produce a file byte-identical to one
already on disk.

A fingerprint is a digest of everything that decides what comes out: the source
media, the clip range, the shape, and the scene. Two projects with the same
fingerprint render the same video, so the second render is work nobody needs.
Anything *not* in the digest is deliberately excluded — the project's title, who
owns it and when it was last edited change nothing about the pixels.

Two properties matter more than the exact algorithm:

- **Key order is irrelevant.** A scene round-tripped through JSON comes back
  with its keys in whatever order the serialiser chose. Fingerprints that
  depended on that order would differ for identical scenes and the whole thing
  would be pointless.
- **Numbers compare by value, not by spelling.** ``0``, ``0.0`` and ``0.000``
  are the same clip start. Storage, JSON and Python each have opinions about how
  to write a float, and none of them should change the answer.
"""

from __future__ import annotations

import hashlib
import json

# Clip times are handed to FFmpeg as `%.3f` and layer geometry is a percentage
# resolved to whole pixels, so a difference below a thousandth cannot reach the
# output. Rounding here means a value that survived a float round-trip one bit
# lighter still matches the render it is identical to.
PRECISION = 3

# The fields that decide the output. Everything else about a project — its
# title, its owner, when it was touched — is metadata.
SIGNIFICANT = ("media_id", "clip_start", "clip_end", "aspect_ratio", "scene")


def _canonical(value):
    """Reduce a value to a form that compares by meaning rather than by spelling."""
    if isinstance(value, bool):
        # Checked before int: bool is an int subclass, and True must not
        # fingerprint as 1.
        return value
    if isinstance(value, (int, float)):
        rounded = round(float(value), PRECISION)
        # Negative zero and zero are the same number; only their sign bit
        # differs, and repr keeps it.
        return rounded + 0.0
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        # Order is meaningful for layers — it is the stacking order — so lists
        # are not sorted, only their contents canonicalised.
        return [_canonical(item) for item in value]
    if value is None:
        return None
    return str(value)


def canonical_form(project: dict) -> dict:
    """The significant part of a project, in a comparable form."""
    source = project if isinstance(project, dict) else {}
    return {key: _canonical(source.get(key)) for key in SIGNIFICANT}


def fingerprint(project: dict) -> str:
    """A stable digest of everything that decides the rendered output.

    SHA-256 rather than a cheaper hash because these are stored and compared
    across restarts: a collision would silently skip a render somebody asked
    for, which is a far worse failure than the cost of hashing a few hundred
    bytes.
    """
    payload = json.dumps(
        canonical_form(project),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_duplicate(existing: object, candidate: str) -> bool:
    """Whether this fingerprint has already been seen.

    Takes any iterable of fingerprints so a caller can pass a set, a list, or
    the result of a query without converting first.
    """
    if not candidate:
        return False
    try:
        return candidate in set(existing or ())
    except TypeError:
        return False


def matches(first: dict, second: dict) -> bool:
    """Whether two projects would render to the same video."""
    return fingerprint(first) == fingerprint(second)
