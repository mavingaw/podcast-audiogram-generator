"""Going back to how a clip was before.

Several things in this application change a project in one click: applying a
template rewrites every layer, cutting words rewrites the audio, a batch
rewrites the lot. Each is worth having and each is uncomfortable without a way
back, so a project keeps its recent past.

The design question is what counts as a revision. Snapshotting every PATCH
would produce hundreds of them — dragging a colour slider is a PATCH per frame
— and a history nobody can read is the same as no history. So a snapshot is
taken of the state *before* a change, and only when the newest snapshot is
already older than `COALESCE_SECONDS`. The effect is a coarse trail of "how it
was a few minutes ago", which is what someone reaching for undo actually wants,
rather than a frame-by-frame log of a drag.

Structural changes ignore that throttle. Applying a template or restoring an
older revision is exactly the moment somebody wants the previous state kept,
however recently the last one was written.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

logger = logging.getLogger(__name__)

# Long enough that a drag becomes one entry, short enough that two separate
# decisions a couple of minutes apart are both recoverable.
COALESCE_SECONDS = float(os.getenv("PAS_REVISION_COALESCE", "90"))

# Per project. A scene is a few kilobytes, so this is cheap; the reason for a
# cap at all is that a history longer than this is not one anybody reads.
KEEP = int(os.getenv("PAS_REVISION_KEEP", "30"))

# Changes that say nothing about the clip itself and are not worth a snapshot.
IGNORED_FIELDS = {"review_state"}


def state_of(project) -> dict:
    """Everything about a project that a restore would put back."""
    return {
        "title": project.title,
        "clip_start": project.clip_start,
        "clip_end": project.clip_end,
        "aspect_ratio": project.aspect_ratio,
        "scene": json.loads(project.scene_json or "{}"),
    }


def digest(state: dict) -> str:
    return hashlib.sha256(
        json.dumps(state, sort_keys=True, default=str).encode()
    ).hexdigest()[:32]


def describe(updates: dict, previous: dict) -> str:
    """A short label for what the change was, for the history list.

    Written from the caller's update rather than by diffing, because "renamed"
    and "moved the clip" are what a person remembers doing, while a diff of the
    scene JSON is a wall of coordinates.
    """
    changed = [key for key in updates if key not in IGNORED_FIELDS]
    if not changed:
        return "Edited"

    if "scene" in changed:
        before = previous.get("scene") or {}
        after = updates.get("scene") or {}
        if len(before.get("layers") or []) != len(after.get("layers") or []):
            return "Changed the layers"
        if (before.get("cuts") or []) != (after.get("cuts") or []):
            return "Cut the transcript"
        if before.get("template") != after.get("template"):
            return "Applied a template"
        if (before.get("music") or {}) != (after.get("music") or {}):
            return "Changed the music"
        return "Changed the design"

    names = {
        "title": "Renamed",
        "clip_start": "Moved the clip",
        "clip_end": "Moved the clip",
        "aspect_ratio": "Changed the shape",
    }
    labels = []
    for key in changed:
        label = names.get(key, "Edited")
        if label not in labels:
            labels.append(label)
    return ", ".join(labels) if labels else "Edited"


def snapshot(db, project, label: str = "Edited", force: bool = False) -> bool:
    """Record how the project is now, before it is changed.

    Returns whether one was written, which is only interesting to tests: a
    caller has nothing useful to do differently either way, and a history that
    failed to record must never be the reason an edit fails.
    """
    from app.db.models import ProjectRevision

    try:
        state = state_of(project)
        current = digest(state)

        latest = db.scalar(
            select(ProjectRevision)
            .where(ProjectRevision.project_id == project.id)
            .order_by(ProjectRevision.created_at.desc())
            .limit(1)
        )
        if latest is not None:
            # The same state twice is not a revision. This happens more than it
            # sounds: a PATCH that sets a field to what it already was, or a
            # save triggered by a field being focused and blurred.
            if latest.digest == current:
                return False
            if not force:
                created = latest.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age = datetime.now(timezone.utc) - created
                if age < timedelta(seconds=COALESCE_SECONDS):
                    # The existing entry already holds an older state, which is
                    # the more useful one to keep.
                    return False

        db.add(ProjectRevision(
            project_id=project.id,
            owner_id=project.owner_id,
            label=label[:80],
            digest=current,
            state_json=json.dumps(state),
        ))
        db.flush()
        prune(db, project.id)
        return True
    except Exception:
        # History is a convenience. It must never be able to fail an edit.
        logger.warning("could not record a revision for %s", project.id, exc_info=True)
        return False


def record(db, project, updates: dict) -> bool:
    """Keep how a project is now, before `updates` are applied to it.

    The single entry point the route uses, so that everything history does —
    reading the current state, deciding on a label, deciding whether this one
    is worth keeping — sits inside one guard. It was previously spread across
    the caller, and a failure in the part outside `snapshot` took the edit down
    with it. History is a convenience; losing it must never lose the change.
    """
    try:
        if not any(key not in IGNORED_FIELDS for key in updates):
            return False
        previous = state_of(project)
        # A template rewrites every layer at once, which is exactly when the
        # previous state is worth keeping however recent the last one is.
        template_changed = (
            "scene" in updates
            and (updates.get("scene") or {}).get("template")
            != (previous.get("scene") or {}).get("template")
        )
        return snapshot(
            db, project,
            label=describe(updates, previous),
            force=template_changed,
        )
    except Exception:
        logger.warning(
            "could not record a revision for %s", getattr(project, "id", "?"),
            exc_info=True,
        )
        return False


def prune(db, project_id: str) -> int:
    """Drop the oldest revisions past the cap."""
    from app.db.models import ProjectRevision

    rows = db.scalars(
        select(ProjectRevision)
        .where(ProjectRevision.project_id == project_id)
        .order_by(ProjectRevision.created_at.desc())
    ).all()
    removed = 0
    for row in rows[KEEP:]:
        db.delete(row)
        removed += 1
    return removed


def restore(db, project, revision) -> None:
    """Put a project back to a recorded state.

    The current state is snapshotted first, unconditionally, so that restoring
    is itself undoable — reaching for history and landing somewhere worse should
    not be a one-way door.
    """
    snapshot(db, project, label="Before restoring", force=True)
    state = json.loads(revision.state_json)
    project.title = state.get("title", project.title)
    project.clip_start = float(state.get("clip_start", project.clip_start))
    project.clip_end = float(state.get("clip_end", project.clip_end))
    project.aspect_ratio = state.get("aspect_ratio", project.aspect_ratio)
    project.scene_json = json.dumps(state.get("scene") or {})
