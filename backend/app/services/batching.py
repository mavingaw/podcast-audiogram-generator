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
from app.services.scene import default_layers
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


def suggestions_for(
    media: MediaAsset,
    transcript: dict,
    count: int,
    soundbites: list[dict] | None = None,
) -> list[dict]:
    """The best moments in an episode, ranked.

    Anything the podcaster marked with `<podcast:soundbite>` goes first and is
    not reordered. Every other suggestion in this application is a guess —
    heuristics about sentence boundaries, a language model reading a shortlist
    — and a soundbite is not a guess: the person who made the episode said
    which part was the good part. Ranking that below our own opinion of it
    would be absurd.
    """
    from app.services import llm

    picked: list[dict] = []
    for bite in soundbites or []:
        try:
            start = float(bite["start"])
            end = start + float(bite["duration"])
        except (KeyError, TypeError, ValueError):
            continue
        if end - start < 1.0:
            continue
        picked.append({
            "start": start,
            "end": end,
            "title": (bite.get("title") or "").strip() or "Soundbite",
            "score": 1.0,
            "reasons": ["the podcaster marked this moment in the feed"],
            "source": "soundbite",
        })
    picked = picked[:count]
    if len(picked) >= count:
        return picked

    raw = decode_peaks(media.peaks_json) if media.peaks_json else None
    peaks = [value / 255.0 for value in raw] if raw else []
    wanted = count - len(picked)
    found = find_clips(
        transcript,
        peaks=peaks,
        duration=media.duration_seconds or transcript.get("duration"),
        # A wider net when a model can read them: the heuristics decide what is
        # shaped like a clip, the model decides which are worth watching, and it
        # needs choices to choose between.
        limit=wanted * 2 if llm.available() else wanted,
    )
    ranked = llm.rerank(found)

    # Do not suggest a moment the podcaster already marked. Compared against
    # the soundbites only: comparing against everything picked so far would
    # make the heuristic suggestions eliminate each other, and overlapping
    # candidates are normal — two good clips often share a sentence.
    marked = list(picked)
    for suggestion in ranked:
        if any(
            suggestion["start"] < bite["end"] and suggestion["end"] > bite["start"]
            for bite in marked
        ):
            continue
        picked.append(suggestion)
        if len(picked) >= count:
            break
    return picked


def with_artwork(scene: dict, artwork_media_id: str) -> dict:
    """Put the show's artwork into a scene that has none of its own.

    Both places it belongs: the background, blurred and dimmed so captions
    stay legible over it, and the artwork slot, sharp. A template that already
    carries a background image or artwork keeps it — that was a deliberate
    choice, and a feed's logo should fill a gap, not overrule one.
    """
    scene = dict(scene)
    background = scene.get("backgroundImage")
    if not (isinstance(background, dict) and background.get("mediaId")):
        scene["backgroundImage"] = {
            "mediaId": artwork_media_id, "blur": 22, "dim": 0.45,
        }
    layers = scene.get("layers")
    if isinstance(layers, list):
        scene["layers"] = [
            {**layer, "mediaId": artwork_media_id}
            if isinstance(layer, dict)
            and layer.get("type") == "artwork"
            and not layer.get("mediaId")
            else layer
            for layer in layers
        ]
    return scene


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
    soundbites: list[dict] | None = None,
    artwork_media_id: str | None = None,
) -> list[Project]:
    """Create clip projects from an episode's best moments.

    Moments already made into a project are skipped, so running this twice adds
    rather than duplicating — which matters more for a feed than for a button,
    because a feed can run again without anybody deciding it should.
    """
    transcript = json.loads(media.transcript_json) if media.transcript_json else None
    if not transcript or not transcript.get("segments"):
        raise BatchError("Transcribe this media first")

    suggestions = suggestions_for(media, transcript, count, soundbites)
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
        if not scene.get("layers"):
            # A clip cut without a person in the loop gets the same stack a
            # person would have been given, so it does not come out as bare
            # captions on a background.
            scene["layers"] = default_layers(aspect_ratio)
        if artwork_media_id:
            scene = with_artwork(scene, artwork_media_id)

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
