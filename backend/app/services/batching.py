"""Turning one episode into a set of clips.

Shared between the button somebody presses and the feed watcher that runs while
nobody is looking, because they should produce identical results — a clip made
automatically must be the same clip you would have made by hand.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select

from app.db.models import Job, JobKind, MediaAsset, Project, Template
from app.services.clipfinder import find as find_clips
from app.services.fingerprint import fingerprint as fingerprint_project
from app.services.snapping import snap as snap_clip_range
from app.services.variants import remap_scene
from app.services.waveform import decode as decode_peaks

log = logging.getLogger(__name__)

# How much two clips must overlap before they count as the same moment. Below
# this they are genuinely different cuts; above it, re-running would just make
# near-duplicates of what is already there.
SAME_MOMENT = 0.6


class BatchError(RuntimeError):
    """Raised when a batch cannot be made. Carries a reason worth showing."""


def suggestions_for(media: MediaAsset, transcript: dict, count: int) -> list[dict]:
    """The best moments in an episode, ranked."""
    from app.services import llm

    raw = decode_peaks(media.peaks_json) if media.peaks_json else None
    peaks = [value / 255.0 for value in raw] if raw else []
    found = find_clips(
        transcript,
        peaks=peaks,
        duration=media.duration_seconds or transcript.get("duration"),
        # A wider net when a model can read them: the heuristics decide what is
        # shaped like a clip, the model decides which are worth watching, and it
        # needs choices to choose between.
        limit=count * 2 if llm.available() else count,
    )
    return llm.rerank(found)[:count]


def make_clips(
    db,
    owner_id: str,
    media: MediaAsset,
    count: int,
    aspect_ratio: str = "9:16",
    template_id: str | None = None,
    render: bool = True,
    source: str = "manual",
    review_state: str = "approved",
) -> list[Project]:
    """Create clip projects from an episode's best moments.

    Moments already made into a project are skipped, so running this twice adds
    rather than duplicating — which matters more for a feed than for a button,
    because a feed can run again without anybody deciding it should.
    """
    transcript = json.loads(media.transcript_json) if media.transcript_json else None
    if not transcript or not transcript.get("segments"):
        raise BatchError("Transcribe this media first")

    suggestions = suggestions_for(media, transcript, count)
    if not suggestions:
        raise BatchError(
            "No passage in this audio is both long enough and self-contained "
            "enough to suggest."
        )

    design: dict | None = None
    source_ratio = aspect_ratio
    if template_id:
        template = db.get(Template, template_id)
        if template and template.owner_id == owner_id:
            design = json.loads(template.scene_json or "{}")
            source_ratio = template.aspect_ratio

    existing = list(
        db.scalars(
            select(Project).where(
                Project.owner_id == owner_id, Project.media_id == media.id
            )
        ).all()
    )

    created: list[Project] = []
    for suggestion in suggestions:
        snapped = snap_clip_range(
            transcript, suggestion["start"], suggestion["end"],
            duration=media.duration_seconds,
        )
        if _already_made(snapped.start, snapped.end, existing):
            continue

        scene = dict(design) if design else {}
        if design and source_ratio != aspect_ratio:
            scene = remap_scene(scene, source_ratio, aspect_ratio)

        project = Project(
            owner_id=owner_id,
            media_id=media.id,
            title=suggestion["title"][:120],
            clip_start=snapped.start,
            clip_end=snapped.end,
            aspect_ratio=aspect_ratio,
            scene_json=json.dumps(scene),
            source=source,
            review_state=review_state,
        )
        db.add(project)
        db.commit()
        created.append(project)
        existing.append(project)

        if render:
            db.add(Job(
                owner_id=owner_id, kind=JobKind.render, subject_id=project.id,
                message="Queued batch render",
                fingerprint=fingerprint_project({
                    "media_id": project.media_id,
                    "clip_start": project.clip_start,
                    "clip_end": project.clip_end,
                    "aspect_ratio": project.aspect_ratio,
                    "scene": scene,
                }),
            ))
            db.commit()

    return created


def _already_made(start: float, end: float, existing: list[Project]) -> bool:
    """Whether this moment is already a project."""
    span = max(0.001, end - start)
    for project in existing:
        overlap = min(end, project.clip_end) - max(start, project.clip_start)
        if overlap <= 0:
            continue
        shorter = min(span, max(0.001, project.clip_end - project.clip_start))
        if overlap / shorter > SAME_MOMENT:
            return True
    return False


def skipped_count(requested: int, created: int) -> int:
    return max(0, requested - created)
