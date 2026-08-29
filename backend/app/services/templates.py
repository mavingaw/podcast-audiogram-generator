"""Saved designs.

A podcast looks the same every week, so the design is the reusable part and the
audio is not. A template therefore keeps the scene's *look* — colours, wave
style, caption preset, layer geometry — and deliberately drops everything that
belongs to one episode: the media a layer points at, and the clip range.

Dropping those is the whole point. A template that carried `mediaId` would put
last week's cover art on this week's clip, and one that carried the clip range
would silently move the selection the moment it was applied.
"""

from __future__ import annotations

import copy

# Scene keys that belong to a particular episode rather than to the design.
# Transcript cuts are source-time ranges into one recording, and effect cues
# sit on one clip's moments; carried into a template they would cut random
# words out of, and drop stingers into, every clip the template touched.
EPISODE_KEYS = ("music", "cuts", "sfx")
# Layer keys that point at a specific upload.
EPISODE_LAYER_KEYS = ("mediaId",)


def scene_for_template(scene: dict, clip_seconds: float | None = None) -> dict:
    """Strip an episode's specifics out of a scene, leaving the design.

    Layer timing is kept, but a layer that ran to the end of the clip it was
    designed on is recorded as running to the end — its endTime is dropped —
    so on a longer clip it does not vanish at the old clip's length, and on a
    shorter one it does not point past the end.
    """
    design = copy.deepcopy(scene) if isinstance(scene, dict) else {}
    for key in EPISODE_KEYS:
        design.pop(key, None)
    if clip_seconds:
        design["templateClipSeconds"] = round(float(clip_seconds), 3)
        for layer in design.get("layers") or []:
            if not isinstance(layer, dict):
                continue
            end = layer.get("endTime")
            try:
                if end is not None and float(end) >= float(clip_seconds) - 0.05:
                    layer.pop("endTime", None)
            except (TypeError, ValueError):
                layer.pop("endTime", None)

    background = design.get("backgroundImage")
    if isinstance(background, dict):
        # Keep the treatment — blur and dim are design — but not the image.
        background.pop("mediaId", None)
        if not background:
            design.pop("backgroundImage", None)

    layers = design.get("layers")
    if isinstance(layers, list):
        for layer in layers:
            if isinstance(layer, dict):
                for key in EPISODE_LAYER_KEYS:
                    layer.pop(key, None)
    return design


def apply_template(scene: dict, template_scene: dict, clip_seconds: float | None = None) -> dict:
    """Lay a template over a project's scene, keeping what the episode owns.

    The project's own media references survive by layer id, so applying a
    design to this week's clip does not drop this week's artwork. Layer
    timing is fitted to this clip: a window that starts past the end is
    pulled back to the start, and one that ends past the end runs to it.
    """
    current = scene if isinstance(scene, dict) else {}
    design = copy.deepcopy(template_scene) if isinstance(template_scene, dict) else {}
    design.pop("templateClipSeconds", None)
    for key in EPISODE_KEYS:
        design.pop(key, None)
    if clip_seconds:
        for layer in design.get("layers") or []:
            if not isinstance(layer, dict):
                continue
            try:
                start = float(layer.get("startTime", 0) or 0)
            except (TypeError, ValueError):
                start = 0.0
            if start >= clip_seconds:
                layer["startTime"] = 0
            try:
                end = layer.get("endTime")
                if end is not None and float(end) > clip_seconds:
                    layer.pop("endTime", None)
            except (TypeError, ValueError):
                layer.pop("endTime", None)

    # Carry the episode's media forward.
    keep_media = {
        layer["id"]: layer.get("mediaId")
        for layer in current.get("layers", [])
        if isinstance(layer, dict) and layer.get("id") and layer.get("mediaId")
    }
    for layer in design.get("layers", []):
        if isinstance(layer, dict) and layer.get("id") in keep_media:
            layer["mediaId"] = keep_media[layer["id"]]

    current_background = current.get("backgroundImage")
    if isinstance(current_background, dict) and current_background.get("mediaId"):
        background = design.setdefault("backgroundImage", {})
        if isinstance(background, dict):
            background["mediaId"] = current_background["mediaId"]

    # The music bed is the episode's, not the template's.
    if isinstance(current.get("music"), dict):
        design["music"] = copy.deepcopy(current["music"])

    return design
