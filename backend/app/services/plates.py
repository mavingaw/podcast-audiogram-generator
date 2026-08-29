"""Pre-rendered still layers ("plates").

FFmpeg does not cache filter output. A background image fed in with ``-loop 1``
is a video stream, so a chain like ``scale,crop,boxblur,scale,eq`` runs once per
frame even though every frame is identical — on a 30-second 1080x1920 clip that
is 900 identical blurs. Measured on a real episode, the background alone
accounted for a third of the render.

Baking each still layer to a PNG once and overlaying the result costs a single
pass and turns the per-frame work into a plain composite. The same applies to
artwork, where the rounded-corner mask is a `geq` — a per-pixel expression
evaluator, and easily the most expensive filter in the graph.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.services.scene import RenderLayer, Scene


class PlateError(RuntimeError):
    pass


@dataclass
class Plates:
    """Baked stills, keyed by what they are for."""

    background: Path | None = None
    artwork: dict[str, Path] | None = None

    def for_layer(self, layer_id: str) -> Path | None:
        return (self.artwork or {}).get(layer_id)


def _run(args: list[str], what: str) -> None:
    completed = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()
        raise PlateError(f"Could not prepare {what}: {detail[-1] if detail else 'unknown error'}")


def bake(
    parsed: Scene,
    images: dict[str, Path],
    width: int,
    height: int,
    work_dir: Path,
) -> Plates:
    """Render every still layer to a PNG under ``work_dir``.

    Failures are raised rather than swallowed: a plate that cannot be built
    would otherwise silently drop the artwork from the export.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    plates = Plates(artwork={})

    background = parsed.background_image
    if background.has_image and images.get(background.media_id):
        target = work_dir / "plate-background.png"
        _run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(images[background.media_id]),
                "-frames:v", "1",
                "-vf", _background_chain(background, width, height),
                str(target),
            ],
            "the background image",
        )
        plates.background = target

    for index, layer in enumerate(parsed.image_layers()):
        source = images.get(layer.media_id or "")
        if source is None:
            continue
        target = work_dir / f"plate-art-{index}.png"
        _run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(source),
                "-frames:v", "1",
                "-vf", _artwork_chain(layer, width, height),
                str(target),
            ],
            f"artwork layer {layer.id}",
        )
        plates.artwork[layer.id] = target

    return plates


def _background_chain(background, width: int, height: int) -> str:
    fit = "increase" if background.fit == "cover" else "decrease"
    if background.blur > 0:
        # Blur a small copy and enlarge it. A box blur costs O(radius) per
        # pixel, so this is both far cheaper and far softer than a large radius
        # at full resolution — and softer is the point: a legible cover behind
        # the captions competes with them.
        small_w = max(48, int(width / 6))
        small_h = max(48, int(height / 6))
        radius = max(1, int(background.blur / 6))
        chain = (
            f"scale={small_w}:{small_h}:force_original_aspect_ratio={fit},"
            f"crop={small_w}:{small_h},"
            f"boxblur=luma_radius={radius}:luma_power=2,"
            f"scale={width}:{height}:flags=bicubic"
        )
    else:
        chain = f"scale={width}:{height}:force_original_aspect_ratio={fit},crop={width}:{height}"
    if background.dim > 0:
        chain += f",eq=brightness=-{background.dim:.2f}"
    return chain + ",format=rgb24"


def _artwork_chain(layer: RenderLayer, width: int, height: int) -> str:
    _, _, box_width, box_height = layer.pixels(width, height)
    chain = (
        f"scale={box_width}:{box_height}:force_original_aspect_ratio=increase,"
        f"crop={box_width}:{box_height}"
    )
    if layer.radius > 0:
        corner = max(1, int(min(box_width, box_height) * layer.radius))
        # FFmpeg has no border-radius, so the corners come from an alpha mask:
        # inside the corner squares, keep only pixels within `corner` of the
        # rounding centre.
        chain += (
            f",format=rgba,geq="
            f"r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            f"a='if(gt(abs(X-(W/2)),W/2-{corner})*gt(abs(Y-(H/2)),H/2-{corner}),"
            f"if(lte(hypot({corner}-(W/2-abs(X-(W/2))),{corner}-(H/2-abs(Y-(H/2)))),{corner}),255,0),255)'"
        )
    else:
        chain += ",format=rgba"
    return chain
