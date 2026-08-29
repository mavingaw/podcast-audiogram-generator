"""Aspect-ratio variants of a project.

One clip usually needs to go to more than one place: 9:16 for TikTok and Reels,
1:1 for a feed, 16:9 for YouTube. Doing that by hand means rebuilding the same
layout three times and keeping them in sync.

A variant is a real project, not a render setting, because the layouts genuinely
differ — a title that fits across a vertical frame is lost in a wide one, and
the safe area a platform covers is not the same shape. Duplicating gives each
one somewhere to diverge.

Geometry is carried over by preserving each layer's *pixel* size and its centre,
rather than its percentages. A waveform 9% tall in a 1920px-high frame is 173px;
the same 9% in a 1080px-square frame is 97px, which reads as a different design.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from app.services.scene import CAPTION_PRESETS, DEFAULT_CAPTION_PRESET

# Long edge first; matches _dimensions in app.services.jobs.
RATIO_DIMENSIONS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "4:5": (1080, 1350),
    "1:1": (1080, 1080),
    "16:9": (1280, 720),
}

# What each ratio is usually for, and the platform guide that suits it.
RATIO_PRESETS: dict[str, dict[str, str]] = {
    "9:16": {"label": "Vertical", "for": "TikTok, Reels, Shorts", "platform": "tiktok"},
    "4:5": {"label": "Portrait", "for": "Instagram feed", "platform": "feed"},
    "1:1": {"label": "Square", "for": "Feed posts", "platform": "feed"},
    "16:9": {"label": "Landscape", "for": "YouTube, X", "platform": ""},
}


class VariantError(ValueError):
    pass


@dataclass(frozen=True)
class Remap:
    source: tuple[int, int]
    target: tuple[int, int]

    @property
    def x_scale(self) -> float:
        return self.source[0] / self.target[0]

    @property
    def y_scale(self) -> float:
        return self.source[1] / self.target[1]


def remap_layer(layer: dict, remap: Remap) -> dict:
    """Move one layer into a new canvas, keeping its size and centre.

    Percentages are relative to the canvas, so carrying them across unchanged
    would resize every layer. Scaling by the dimension ratio keeps the layer the
    same number of pixels, which is what keeps a design recognisable.
    """
    moved = copy.deepcopy(layer)

    width = _number(layer.get("width"), 60.0) * remap.x_scale
    height = _number(layer.get("height"), 10.0) * remap.y_scale
    # A layer wider than the frame is worse than one that has been shrunk.
    width = min(width, 100.0)
    height = min(height, 100.0)

    centre_x = _number(layer.get("x"), 0.0) + _number(layer.get("width"), 60.0) / 2
    centre_y = _number(layer.get("y"), 0.0) + _number(layer.get("height"), 10.0) / 2

    moved["width"] = round(width, 3)
    moved["height"] = round(height, 3)
    moved["x"] = round(_clamp(centre_x - width / 2, 0.0, 100.0 - width), 3)
    moved["y"] = round(_clamp(centre_y - height / 2, 0.0, 100.0 - height), 3)
    return moved


# The bottom of a vertical frame belongs to the platform's own interface. In
# landscape there is no such chrome, so only the frame edge applies.
SAFE_BOTTOM = {"9:16": 0.80, "4:5": 0.80, "1:1": 0.80, "16:9": 0.94}
# A visible gap between the last caption line and the waveform, as a share of
# height. Touching reads as a collision even when it technically is not.
CAPTION_GAP = 0.01
MIN_WAVE_HEIGHT = 5.0


def settle_waveform(scene: dict, target_ratio: str) -> dict:
    """Keep the waveform clear of the captions after a shape change.

    Remapping preserves a layer's pixel size and centre, which is right for
    artwork and titles but not for the waveform: captions are positioned by a
    margin that is a share of *height*, so changing shape moves the caption band
    underneath a waveform that stayed put. A default 9:16 scene varied into
    landscape put the waveform straight through its own captions.

    So the waveform is pushed below the caption band, and shrunk rather than
    allowed to run into the platform's interface at the bottom.
    """
    layers = scene.get("layers")
    if not isinstance(layers, list):
        return scene

    preset_name = str(scene.get("captionPreset") or DEFAULT_CAPTION_PRESET)
    preset = CAPTION_PRESETS.get(preset_name, CAPTION_PRESETS[DEFAULT_CAPTION_PRESET])
    # Captions are drawn up from the bottom edge, so this is where they end.
    captions_end = (1.0 - preset["margin_ratio"]) + CAPTION_GAP
    floor = SAFE_BOTTOM.get(target_ratio, 0.80)

    for layer in layers:
        if not isinstance(layer, dict) or layer.get("type") != "waveform":
            continue
        top = float(layer.get("y", 0.0)) / 100
        height = float(layer.get("height", 0.0)) / 100
        if top >= captions_end and top + height <= floor:
            continue  # already clear

        top = max(top, captions_end)
        height = min(height, max(MIN_WAVE_HEIGHT / 100, floor - top))
        layer["y"] = round(top * 100, 3)
        layer["height"] = round(height * 100, 3)
    return scene


def remap_scene(scene: dict, source_ratio: str, target_ratio: str) -> dict:
    """Carry a scene into another aspect ratio."""
    if source_ratio not in RATIO_DIMENSIONS:
        raise VariantError(f"Unknown source ratio: {source_ratio}")
    if target_ratio not in RATIO_DIMENSIONS:
        raise VariantError(f"Unknown target ratio: {target_ratio}")

    remap = Remap(RATIO_DIMENSIONS[source_ratio], RATIO_DIMENSIONS[target_ratio])
    moved = copy.deepcopy(scene) if isinstance(scene, dict) else {}

    layers = moved.get("layers")
    if isinstance(layers, list):
        moved["layers"] = [
            remap_layer(layer, remap) if isinstance(layer, dict) else layer
            for layer in layers
        ]

    # The platform guide belongs to the shape, not to the clip.
    preset = RATIO_PRESETS.get(target_ratio, {})
    if preset.get("platform"):
        moved["platform"] = preset["platform"]
    else:
        moved.pop("platform", None)
    return settle_waveform(moved, target_ratio)


def variant_title(title: str, target_ratio: str) -> str:
    label = RATIO_PRESETS.get(target_ratio, {}).get("label", target_ratio)
    # Do not stack suffixes when a variant is itself varied.
    for existing in RATIO_PRESETS.values():
        suffix = f" ({existing['label']})"
        if title.endswith(suffix):
            title = title[: -len(suffix)]
            break
    return f"{title} ({label})"


def _number(value: object, fallback: float) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return fallback if number != number else number


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(max(low, high), value))
