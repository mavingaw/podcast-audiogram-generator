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
EPISODE_KEYS = ("music",)
# Layer keys that point at a specific upload.
EPISODE_LAYER_KEYS = ("mediaId",)


def scene_for_template(scene: dict) -> dict:
    """Strip an episode's specifics out of a scene, leaving the design."""
    design = copy.deepcopy(scene) if isinstance(scene, dict) else {}
    for key in EPISODE_KEYS:
        design.pop(key, None)

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


def apply_template(scene: dict, template_scene: dict) -> dict:
    """Lay a template over a project's scene, keeping what the episode owns.

    The project's own media references survive by layer id, so applying a
    design to this week's clip does not drop this week's artwork.
    """
    current = scene if isinstance(scene, dict) else {}
    design = copy.deepcopy(template_scene) if isinstance(template_scene, dict) else {}

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
