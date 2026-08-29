"""Render the Kinder app icon to PNG, with nothing but the standard library.

Unraid shows a container's icon on the Docker and Dashboard tabs, and it wants a
raster image. Neither this server nor the app image carries an SVG rasteriser —
no ImageMagick, no librsvg, no Pillow — and adding one to render a 256-pixel
square once would be silly. zlib and struct are enough to write a PNG, and the
mark is simple geometry: a rounded obsidian tile, the baby-blue spine, and the
gold sweep across it.

    python3 make_icon.py kinder-icon.png [size]
"""

from __future__ import annotations

import struct
import sys
import zlib

# The brand, from docs/BRAND.md.
OBSIDIAN = (0x0B, 0x0D, 0x11)
SURFACE = (0x16, 0x1B, 0x22)
BORDER = (0x26, 0x2C, 0x36)
BLUE = (0x89, 0xCF, 0xF0)
BLUE_LIGHT = (0xD4, 0xEE, 0xFC)
GOLD = (0xD4, 0xAF, 0x37)
GOLD_LIGHT = (0xF3, 0xE5, 0xAB)


def mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rounded_rect_coverage(x, y, size, radius, inset=0.0):
    """How much of this pixel is inside the rounded square, 0..1.

    Sampled rather than computed exactly: four samples per pixel is enough to
    take the jaggedness off a corner at this size, and the whole image is a few
    thousand pixels.
    """
    lo = inset
    hi = size - inset
    hits = 0
    for dx, dy in ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)):
        px, py = x + dx, y + dy
        if not (lo <= px <= hi and lo <= py <= hi):
            continue
        # Distance into a corner, if this point is in one.
        cx = lo + radius if px < lo + radius else (hi - radius if px > hi - radius else px)
        cy = lo + radius if py < lo + radius else (hi - radius if py > hi - radius else py)
        if (px - cx) ** 2 + (py - cy) ** 2 <= radius * radius:
            hits += 1
    return hits / 4.0


def bar_coverage(x, y, left, top, right, bottom, radius):
    """Coverage of a vertical rounded bar."""
    hits = 0
    for dx, dy in ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)):
        px, py = x + dx, y + dy
        if not (left <= px <= right and top <= py <= bottom):
            continue
        cx = min(max(px, left + radius), right - radius)
        cy = min(max(py, top + radius), bottom - radius)
        if (px - cx) ** 2 + (py - cy) ** 2 <= radius * radius:
            hits += 1
    return hits / 4.0


def sweep_coverage(x, y, size):
    """The gold arc sweeping up and right from the spine.

    A band between two parallel lines, clipped to a wedge — close enough to the
    vector mark at icon size, and it reads as motion rather than as a shape.
    """
    hits = 0
    for dx, dy in ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)):
        px, py = (x + dx) / size, (y + dy) / size
        # Line running from lower-left to upper-right.
        d = py + 0.62 * px - 0.78
        if -0.085 <= d <= 0.085 and 0.36 <= px <= 0.86 and 0.20 <= py <= 0.70:
            hits += 1
    return hits / 4.0


def render(size: int) -> bytes:
    rows = []
    radius = size * 0.235
    spine_left = size * 0.255
    spine_right = size * 0.375
    spine_top = size * 0.235
    spine_bottom = size * 0.775
    spine_radius = (spine_right - spine_left) / 2

    for y in range(size):
        row = bytearray()
        for x in range(size):
            tile = rounded_rect_coverage(x, y, size, radius)
            if tile <= 0:
                row += bytes((0, 0, 0, 0))
                continue

            # The tile itself: a soft diagonal from jet slate to obsidian, so it
            # does not read as a flat block next to other app icons.
            base = mix(SURFACE, OBSIDIAN, (x + y) / (2 * size))
            # A hairline edge, the same charcoal the app uses for its borders.
            edge = rounded_rect_coverage(x, y, size, radius - size * 0.012,
                                         inset=size * 0.012)
            colour = mix(BORDER, base, edge)

            sweep = sweep_coverage(x, y, size)
            if sweep > 0:
                colour = mix(colour, mix(GOLD, GOLD_LIGHT, (y / size) * 0.7), sweep)

            spine = bar_coverage(x, y, spine_left, spine_top, spine_right,
                                 spine_bottom, spine_radius)
            if spine > 0:
                colour = mix(colour, mix(BLUE_LIGHT, BLUE, y / size), spine)

            row += bytes((*colour, round(255 * tile)))
        rows.append(bytes(row))

    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "kinder-icon.png"
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 256
    data = render(size)
    with open(target, "wb") as handle:
        handle.write(data)
    print(f"wrote {target} ({size}x{size}, {len(data)} bytes)")
