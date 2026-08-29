"""Expiring old renders.

Every export is an artifact that lives forever. A podcast worked on weekly puts
a few hundred megabytes a month on the volume and nothing ever takes any of it
off again, so the only question is how long the disk lasts, not whether it fills.

**This is off by default and must stay that way.** Deleting somebody's finished
work because a background thread decided it was old is not a behaviour to
inherit by upgrading. `PAS_RETENTION_DAYS` defaults to 0, which disables the
sweep entirely; a deployment only starts expiring renders when its operator
says a number out loud.

Two protections apply once it is on:

- **An age limit, not a size limit.** Sweeping to hit a disk target means the
  amount you keep depends on how big your recent renders happened to be, which
  is impossible to reason about. "Nothing older than sixty days" is a promise
  somebody can hold in their head.
- **A floor on how much survives.** `PAS_RETENTION_KEEP` renders are kept
  whatever their age, so a misjudged `PAS_RETENTION_DAYS=1` on a project nobody
  touched last week cannot empty the library.

Environment:

- ``PAS_RETENTION_DAYS`` — expire renders older than this many days. ``0``
  (the default) disables the sweep.
- ``PAS_RETENTION_KEEP`` — how many of the most recent renders survive
  regardless of age. Defaults to 20.
- ``PAS_RETENTION_INTERVAL_HOURS`` — how often the sweep runs. Defaults to 24.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_KEEP = 20
DEFAULT_INTERVAL_HOURS = 24.0


def retention_days() -> int:
    """How old a render may get. Zero means the sweep is disabled."""
    try:
        return max(0, int(os.getenv("PAS_RETENTION_DAYS", "0")))
    except ValueError:
        # A typo in an environment variable must not start deleting things.
        log.warning("PAS_RETENTION_DAYS is not a number; retention stays disabled")
        return 0


def retention_keep() -> int:
    try:
        return max(0, int(os.getenv("PAS_RETENTION_KEEP", str(DEFAULT_KEEP))))
    except ValueError:
        return DEFAULT_KEEP


def retention_interval_hours() -> float:
    try:
        return max(0.5, float(os.getenv("PAS_RETENTION_INTERVAL_HOURS",
                                        str(DEFAULT_INTERVAL_HOURS))))
    except ValueError:
        return DEFAULT_INTERVAL_HOURS


@dataclass
class Candidate:
    """One render directory the sweep is considering."""

    project_id: str
    path: Path
    modified: float
    size_bytes: int
    orphaned: bool

    @property
    def age_days(self) -> float:
        return max(0.0, (time.time() - self.modified) / 86400.0)


@dataclass
class Summary:
    """What a sweep found, and what it did about it."""

    enabled: bool = False
    dry_run: bool = False
    scanned: int = 0
    removed: list[str] = field(default_factory=list)
    kept: int = 0
    freed_bytes: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "dry_run": self.dry_run,
            "scanned": self.scanned,
            "removed": self.removed,
            "removed_count": len(self.removed),
            "kept": self.kept,
            "freed_bytes": self.freed_bytes,
            "errors": self.errors,
        }


def _directory_size(path: Path) -> int:
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                total += entry.stat().st_size
    except OSError:
        pass
    return total


def _last_touched(path: Path) -> float:
    """When this render was last written.

    The directory's own mtime is not enough: on some filesystems it reflects
    when the last entry was added rather than when the files were written, and a
    republished export updates a file inside without touching the directory. The
    newest thing in it is the honest answer to "when did this render happen".
    """
    newest = 0.0
    try:
        newest = path.stat().st_mtime
        for entry in path.rglob("*"):
            try:
                newest = max(newest, entry.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        pass
    return newest


def live_project_ids() -> set[str]:
    """Projects that still exist.

    Imported here rather than at module scope because the test harness reloads
    the database modules onto a per-test database; a binding taken at import
    time would point at whichever database happened to be current first.
    """
    try:
        from app.db.models import Project
        from app.db.session import SessionLocal
        from sqlalchemy import select

        with SessionLocal() as db:
            return set(db.scalars(select(Project.id)).all())
    except Exception as error:
        # Without the database we cannot tell a live render from an orphan, and
        # guessing means deleting somebody's work. Report nothing live, which
        # the caller below treats as "do not sweep".
        log.warning("Retention could not read the project list: %s", error)
        raise


def collect(outputs_dir: Path, known_projects: set[str]) -> list[Candidate]:
    """Every render directory, newest first."""
    if not outputs_dir.is_dir():
        return []
    found: list[Candidate] = []
    for entry in outputs_dir.iterdir():
        if not entry.is_dir():
            continue
        found.append(
            Candidate(
                project_id=entry.name,
                path=entry,
                modified=_last_touched(entry),
                size_bytes=_directory_size(entry),
                orphaned=entry.name not in known_projects,
            )
        )
    found.sort(key=lambda item: item.modified, reverse=True)
    return found


def sweep(
    outputs_dir: Path,
    max_age_days: int | None = None,
    keep_minimum: int | None = None,
    known_projects: set[str] | None = None,
    dry_run: bool = False,
) -> dict:
    """Expire old renders, keeping the most recent whatever their age.

    ``known_projects`` is the set of project ids that still exist. Passing it
    explicitly keeps this function testable without a database; omitting it
    looks the list up, and a lookup that fails aborts the sweep rather than
    treating every render as an orphan.

    An orphaned directory — one whose project has been deleted — is removed
    without waiting for the age limit, because the person deleting the project
    already said they were finished with it. The keep-minimum floor exists to
    protect against a *mistimed age limit*, so it does not apply to work that
    was explicitly thrown away.
    """
    days = retention_days() if max_age_days is None else max_age_days
    keep = retention_keep() if keep_minimum is None else keep_minimum
    summary = Summary(enabled=days > 0, dry_run=dry_run)

    if days <= 0:
        return summary.as_dict()

    if known_projects is None:
        try:
            known_projects = live_project_ids()
        except Exception:
            summary.errors.append("could not read the project list; nothing swept")
            return summary.as_dict()

    candidates = collect(Path(outputs_dir), known_projects)
    summary.scanned = len(candidates)

    # Newest first, so the first `keep` live renders are the protected ones.
    protected = 0
    for candidate in candidates:
        if candidate.orphaned:
            expired = True
        else:
            if protected < keep:
                protected += 1
                summary.kept += 1
                continue
            expired = candidate.age_days > days

        if not expired:
            summary.kept += 1
            continue

        summary.removed.append(candidate.project_id)
        summary.freed_bytes += candidate.size_bytes
        if dry_run:
            continue
        try:
            shutil.rmtree(candidate.path)
        except OSError as error:
            summary.errors.append(f"{candidate.project_id}: {error}")

    if summary.removed and not dry_run:
        log.info(
            "Retention removed %d render(s), freeing %.1f MB",
            len(summary.removed), summary.freed_bytes / (1024 * 1024),
        )
    return summary.as_dict()


def sweep_now() -> dict:
    """Run one sweep against the configured outputs directory."""
    from app.core.config import settings

    return sweep(settings.outputs_dir)


def _loop() -> None:
    interval = retention_interval_hours() * 3600.0
    while True:
        try:
            sweep_now()
        except Exception as error:
            # Housekeeping must never take the application down with it.
            log.warning("Retention sweep failed: %s", error)
        time.sleep(interval)


def start(background: bool = True) -> bool:
    """Begin the periodic sweep, if retention is switched on.

    Returns whether anything was started, so start-up can say so. A disabled
    sweep starts no thread at all rather than one that wakes to do nothing.
    """
    try:
        if retention_days() <= 0:
            return False
        if not background:
            sweep_now()
            return True
        thread = threading.Thread(target=_loop, name="pas-retention", daemon=True)
        thread.start()
        log.info(
            "Retention enabled: renders older than %d days expire, keeping the "
            "most recent %d", retention_days(), retention_keep(),
        )
        return True
    except Exception as error:
        log.warning("Could not start the retention sweep: %s", error)
        return False
