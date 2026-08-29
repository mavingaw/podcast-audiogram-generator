"""The project scene: layers, timing, and the wave style.

The editor stores layers as percentages of the canvas and seconds relative to
the clip. The renderer needs pixels and FFmpeg expressions, and until now it
ignored both — every export drew one hard-coded waveform and the captions, so
what you arranged on the canvas was not what came out of the encoder.

This module is the single translation between the two, which is why the
placement maths lives here rather than being duplicated per filter.

Wave styles follow the reference application's vocabulary — a wave is `none`,
bars, or a line — rather than our previous single hard-coded look.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# FFmpeg's drawtext takes a filename or an escaped literal; escaping is safer
# than writing a temp file per layer, but the escape set is fiddly enough to
# be worth naming.
_DRAWTEXT_ESCAPES = {
    "\\": r"\\",
    ":": r"\:",
    "'": r"\'",
    "%": r"\%",
    "\n": " ",
    "\r": "",
}

# showwaves has no bar mode; its modes are point, line, p2p, and cline.
#
# Its `n` option is not the way to widen bars: `n` is samples per column, so the
# filter consumes `n * width` samples per *frame*. A large `n` makes one frame
# span more audio than the whole clip and the filter emits nothing at all.
#
# Real bars come from rendering the wave into a narrow buffer and scaling it up
# with nearest-neighbour, which turns each column into a solid block. The second
# value below is that block width in pixels; 1 means draw at full resolution.
# The Kinder brand palette, from the asset bundle's design system. Kept here
# because the render is the product: an exported clip is what people actually
# see, so a new project should already look like Kinder without anyone touching
# a colour picker.
BRAND = {
    "obsidian": "#0B0D11",   # Obsidian Black — the canvas
    "surface": "#161B22",    # Jet Slate
    "blue": "#89CFF0",       # Baby Blue — primary accent, the waveform
    "blueLight": "#D4EEFC",  # Ice Glow
    "gold": "#D4AF37",       # Champagne Gold — peaks and the progress bar
    "goldLight": "#F3E5AB",  # Warm Ochre
    "offWhite": "#F8FAFC",
}
DEFAULT_BACKGROUND = BRAND["obsidian"]
DEFAULT_ACCENT = BRAND["blue"]
DEFAULT_CAPTION_COLOR = BRAND["offWhite"]

# What share of bars may be drawn in the peak colour. A threshold against the
# clip's loudest moment was the obvious rule and the wrong one: podcast audio is
# heavily compressed, so most bars sit near the maximum and nearly all of them
# qualified — a wall of gold rather than an accent. Ranking instead keeps the
# gold sparse no matter how squashed the source is.
PEAK_SHARE = 0.15

# A peak must also stand above at least one of its neighbours. Without this a
# constant tone — a test signal, a music bed under silence — has every bar tie
# for loudest and the top slice becomes an arbitrary gold block.


WAVE_STYLES = {
    # name -> (showwaves mode, bar width in px, height multiplier)
    "line": ("cline", 1, 1.0),
    "bars": ("cline", 8, 1.0),
    "wideBars": ("cline", 18, 1.25),
    "edge": ("line", 1, 1.0),
    "points": ("point", 1, 1.0),
}
# `envelope` is the default because it is the only style whose proportions we
# control. showwaves maps *instantaneous sample amplitude* to height, and at any
# sane frame rate each output column spans only a couple of samples, so even a
# full-scale tone draws a thin ribbon rather than a full bar. Drawing from the
# peak envelope we already extract gives a waveform that always fills its box
# and reads at a glance in a feed.
ENVELOPE_STYLES = {
    # name -> (bar count, gap as a fraction of the bar pitch, rounded)
    "envelope": (58, 0.34, False),
    "envelopeFine": (104, 0.28, False),
    "envelopeChunky": (34, 0.40, False),
}
DEFAULT_WAVE_STYLE = "envelope"

# showwaves' amplitude curve. Speech spends most of its time well below peak, so
# a linear envelope draws a mostly-flat line with occasional spikes; the square
# root curve lifts conversational level into the middle of the box, which is the
# look audiogram tools ship. Dense, already-compressed music saturates under
# sqrt, so the choice is exposed rather than fixed.
WAVE_SCALES = ("sqrt", "lin", "log", "cbrt")
DEFAULT_WAVE_SCALE = "sqrt"

# Shown in the editor's style picker.
WAVE_STYLE_LABELS = {
    "envelope": "Bars",
    "envelopeFine": "Fine bars",
    "envelopeChunky": "Chunky bars",
    "line": "Centred line",
    "bars": "Bars",
    "wideBars": "Wide bars",
    "edge": "Edge",
    "points": "Points",
    "none": "No waveform",
}

RENDERABLE_TEXT_TYPES = {"title", "text", "captions"}

# The default stack, per shape: artwork, then title, then the caption band,
# then the waveform. One layout cannot serve every ratio because captions are
# sized from frame *width* but positioned by a margin in frame *height*, so
# the band they occupy grows enormously as a frame gets wider.
#
# Mirrored by LAYOUT in frontend/src/App.tsx, and held to the caption band by
# test_the_default_layout_never_overlaps_in_any_shape. Owned here because the
# renderer, the batch and the feed watcher all need it: a clip cut without a
# person in the loop used to render with no artwork slot and no waveform at
# all, while its preview showed both — the frontend filled in the defaults
# that the backend never had.
DEFAULT_LAYOUT = {
    "9:16": {"artwork": (12, 32), "title": (46, 8), "waveform": (71, 9)},
    "4:5": {"artwork": (10, 29), "title": (41, 8), "waveform": (71, 9)},
    "1:1": {"artwork": (8, 27), "title": (37, 8), "waveform": (71, 9)},
    "16:9": {"artwork": (4, 14), "title": (20, 8), "waveform": (80, 12)},
}


def caption_band_percent(preset_name: str, aspect_ratio: str, lines: int = 2) -> tuple[float, float]:
    """Where the burned-in caption block sits, as (top, height) in percent."""
    preset = CAPTION_PRESETS.get(preset_name) or CAPTION_PRESETS[DEFAULT_CAPTION_PRESET]
    # Only the shape matters here, not the pixel size; the ratio is enough,
    # and keeps this module from importing the one that owns the dimensions.
    width, height = {
        "9:16": (9, 16), "4:5": (4, 5), "1:1": (1, 1), "16:9": (16, 9),
    }.get(aspect_ratio, (9, 16))
    block = lines * preset["size_ratio"] * (width / height)
    bottom = 1 - preset["margin_ratio"]
    top = bottom - block
    return round(top * 100, 2), round(block * 100, 2)


def default_layers(aspect_ratio: str = "9:16", title_text: str = "{{episode}}") -> list[dict]:
    """The stack a new clip gets when nothing has chosen one.

    The title carries the {{episode}} token rather than placeholder text, so a
    clip cut by a feed comes out named after its episode and an upload, which
    has no episode, simply shows no title rather than "Episode Title".
    """
    layout = DEFAULT_LAYOUT.get(aspect_ratio, DEFAULT_LAYOUT["9:16"])
    art_y, art_h = layout["artwork"]
    title_y, title_h = layout["title"]
    wave_y, wave_h = layout["waveform"]
    cap_y, cap_h = caption_band_percent(DEFAULT_CAPTION_PRESET, aspect_ratio)
    base = {"visible": True, "locked": False, "startTime": 0}
    return [
        {"id": "background", "name": "Background", "type": "background",
         "x": 0, "y": 0, "width": 100, "height": 100, **base, "locked": True},
        {"id": "artwork", "name": "Podcast Artwork", "type": "artwork",
         "x": 12, "y": art_y, "width": 76, "height": art_h, **base},
        {"id": "waveform", "name": "Waveform", "type": "waveform",
         "x": 12, "y": wave_y, "width": 76, "height": wave_h, **base},
        {"id": "title", "name": "Episode Title", "type": "title",
         "x": 12, "y": title_y, "width": 76, "height": title_h, **base,
         "text": title_text},
        {"id": "captions", "name": "Captions", "type": "captions",
         "x": 12, "y": cap_y, "width": 76, "height": cap_h, **base,
         "text": "Your story, in motion."},
    ]

# Caption presets tuned for feeds rather than for television. Most of a social
# audience watches muted, so captions are the content, not an accessory: large,
# heavy, high-contrast, and kept clear of the platform's own chrome.
#
# `size_ratio` is a fraction of the frame *width*, not its height. Height would
# seem natural but breaks across shapes: the same clip rendered 9:16 and 16:9
# would get 100px and 37px captions, because 720 is a much shorter frame than
# 1920. Reading size tracks how wide the line can run, so width is the honest
# reference and a landscape variant stays as legible as the vertical one.
#
# `margin_ratio` stays a fraction of height, because the band a platform covers
# with its own interface is vertical.
CAPTION_PRESETS = {
    "social": {
        "label": "Social — big and bold",
        # The word being spoken, when word highlighting is on.
        "highlight": BRAND["gold"],
        "size_ratio": 0.092,
        "outline": 5,
        "shadow": 0,
        "bold": True,
        "back_alpha": "00",
        "margin_ratio": 0.30,
        "uppercase": False,
    },
    "boxed": {
        "label": "Boxed — solid plate",
        "highlight": BRAND["blue"],
        "size_ratio": 0.071,
        "outline": 2,
        "shadow": 0,
        "bold": True,
        "back_alpha": "B4",
        # At 0.26 the plate reached 74% of the frame and ran into the waveform.
        "margin_ratio": 0.31,
        "uppercase": False,
    },
    "shout": {
        "label": "Shout — uppercase impact",
        "highlight": BRAND["gold"],
        "size_ratio": 0.103,
        "outline": 6,
        "shadow": 0,
        "bold": True,
        "back_alpha": "00",
        "margin_ratio": 0.32,
        "uppercase": True,
    },
    "kinder": {
        "label": "Kinder — baby blue plate",
        # Obsidian type on a baby-blue plate, so the spoken word goes gold.
        "highlight": BRAND["goldDark"] if "goldDark" in BRAND else BRAND["gold"],
        "size_ratio": 0.074,
        "outline": 2,
        "shadow": 0,
        "bold": True,
        # A solid plate rather than an outline: the brand's karaoke style sets
        # obsidian type on baby blue, which only reads if the plate is opaque.
        "back_alpha": "00",
        "margin_ratio": 0.31,
        "uppercase": False,
        # When set, the plate is drawn in this colour and the text in the
        # scene's background colour, inverting the usual arrangement.
        "plate": BRAND["blue"],
        "plate_text": BRAND["obsidian"],
    },
    "clean": {
        "label": "Clean — understated",
        "highlight": BRAND["blue"],
        "size_ratio": 0.053,
        "outline": 2,
        "shadow": 1,
        "bold": False,
        "back_alpha": "99",
        # 0.10 read as "understated means low in frame", but at that margin the
        # captions ran through the waveform in every aspect ratio and sat inside
        # the bottom fifth that platforms cover with their own interface. What
        # makes this preset understated is its size and its lack of a plate, not
        # its position, so it clears both like the rest.
        "margin_ratio": 0.31,
        "uppercase": False,
    },
}
DEFAULT_CAPTION_PRESET = "social"

# Horizontal margin each side, as a share of width. Also decides how much room
# a caption line actually has.
CAPTION_MARGIN_RATIO = 0.08
# A bold sans glyph averages about half its point size in width. Rough, but the
# alternative is measuring the font, and being roughly right here is the
# difference between one line and three.
GLYPH_WIDTH_FACTOR = 0.5


def caption_char_budget(preset_name: str) -> int:
    """How many characters actually fit on one caption line.

    The splitter and the renderer have to agree on this. They did not: lines
    were cut at a flat 42 characters while the burned-in font only fit about
    18, so libass re-wrapped every line into three and the block grew tall
    enough to collide with the waveform above it.
    """
    preset = CAPTION_PRESETS.get(preset_name, CAPTION_PRESETS[DEFAULT_CAPTION_PRESET])
    usable = 1.0 - (2 * CAPTION_MARGIN_RATIO)
    per_char = preset["size_ratio"] * GLYPH_WIDTH_FACTOR
    return max(12, int(usable / per_char))

# Fraction of the frame each platform covers with its own interface. Captions
# and text sit above this band or they are simply not read.
PLATFORM_SAFE_AREAS = {
    "tiktok": {"label": "TikTok", "bottom": 0.22, "top": 0.10, "right": 0.16},
    "reels": {"label": "Instagram Reels", "bottom": 0.20, "top": 0.10, "right": 0.14},
    "shorts": {"label": "YouTube Shorts", "bottom": 0.16, "top": 0.08, "right": 0.14},
    "feed": {"label": "Feed / square", "bottom": 0.06, "top": 0.06, "right": 0.06},
}


@dataclass
class RenderLayer:
    """One canvas layer resolved into render coordinates."""

    id: str
    type: str
    name: str
    text: str
    # None means 'inherit the scene accent' rather than 'white'.
    color: str | None
    # Percentages of the canvas, as the editor stores them.
    x: float
    y: float
    width: float
    height: float
    start: float
    end: float
    visible: bool = True
    # Set on artwork layers; names the uploaded image to draw.
    media_id: str | None = None
    # Artwork corner rounding, as a fraction of the shorter side.
    radius: float = 0.0

    def paint(self, fallback: str) -> str:
        """The layer's colour, or the scene value it inherits when unset."""
        return self.color or fallback

    def pixels(self, canvas_width: int, canvas_height: int) -> tuple[int, int, int, int]:
        return (
            int(round(self.x / 100 * canvas_width)),
            int(round(self.y / 100 * canvas_height)),
            max(1, int(round(self.width / 100 * canvas_width))),
            max(1, int(round(self.height / 100 * canvas_height))),
        )


@dataclass
class Background:
    """The full-bleed backdrop behind everything else.

    An uploaded image, or the show's own cover art, blurred and dimmed — the
    look most podcast audiograms use, because it carries the show's colours
    without competing with the captions.
    """

    color: str = "#101820"
    media_id: str | None = None
    blur: float = 0.0
    dim: float = 0.35
    fit: str = "cover"

    @property
    def has_image(self) -> bool:
        return bool(self.media_id)


@dataclass
class Scene:
    background: str = DEFAULT_BACKGROUND
    accent: str = DEFAULT_ACCENT
    # Loud moments in the waveform. None keeps every bar the accent colour.
    peak_accent: str | None = BRAND["gold"]
    wave_style: str = DEFAULT_WAVE_STYLE
    wave_scale: str = DEFAULT_WAVE_SCALE
    caption_preset: str = DEFAULT_CAPTION_PRESET
    caption_color: str = DEFAULT_CAPTION_COLOR
    # Light each word as it is spoken. On by default: it is what a social
    # caption looks like now, and the word timings are already there.
    word_highlight: bool = True

    # Nudge every caption earlier or later against the audio.
    #
    # Word timings come out of Whisper accurate to a few tens of milliseconds
    # most of the time, and occasionally not: a noisy passage, an overlapping
    # speaker, a hard accent. When a whole clip's captions sit consistently
    # behind or ahead of the voice there is nothing to fix word by word, and
    # re-transcribing rarely helps. One number does.
    #
    # Positive puts the captions later, which is the common direction — the
    # transcriber tends to mark a word once it is confident, slightly after it
    # began.
    caption_offset: float = 0.0

    # The voice track's own level and edges.
    #
    # A clip cut out of the middle of an episode starts and ends abruptly, and
    # the loudness pass cannot help with that: it sets the level of the whole
    # file, not what happens in its first quarter-second. A short fade is the
    # difference between a clip that begins and one that is spliced.
    voice_gain_db: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    background_image: Background = field(default_factory=Background)
    layers: list[RenderLayer] = field(default_factory=list)

    def waveform_layer(self) -> RenderLayer | None:
        return next((layer for layer in self.layers if layer.type == "waveform" and layer.visible), None)

    def image_layers(self) -> list[RenderLayer]:
        """Artwork layers that reference an uploaded image, in stacking order."""
        return [
            layer
            for layer in self.layers
            if layer.visible and layer.type in {"artwork", "image"} and layer.media_id
        ]

    def progress_layer(self) -> RenderLayer | None:
        return next((layer for layer in self.layers if layer.type == "progress" and layer.visible), None)

    def text_layers(self) -> list[RenderLayer]:
        return [
            layer
            for layer in self.layers
            if layer.visible and layer.type in RENDERABLE_TEXT_TYPES and layer.text.strip()
        ]


def parse(
    scene: dict | None, clip_duration: float, aspect_ratio: str = "9:16"
) -> Scene:
    """Read a stored scene into render coordinates, clamping nonsense values.

    `aspect_ratio` only matters when the scene has no layers of its own: the
    default stack differs per shape.
    """
    scene = scene if isinstance(scene, dict) else {}
    style = str(scene.get("waveStyle") or DEFAULT_WAVE_STYLE)
    if style not in WAVE_STYLES and style not in ENVELOPE_STYLES and style != "none":
        style = DEFAULT_WAVE_STYLE

    raw_layers = scene.get("layers")
    if raw_layers is None:
        # The preview fills in the default stack for a project with no layers;
        # the render drew nothing at all. Same stack, so the two agree. Only
        # when the key is absent: an explicit empty list is a choice.
        raw_layers = default_layers(aspect_ratio)
    layers: list[RenderLayer] = []
    if isinstance(raw_layers, list):
        for entry in raw_layers:
            layer = _layer(entry, clip_duration)
            if layer is not None:
                layers.append(layer)

    scale = str(scene.get("waveScale") or DEFAULT_WAVE_SCALE)
    if scale not in WAVE_SCALES:
        scale = DEFAULT_WAVE_SCALE

    preset = str(scene.get("captionPreset") or DEFAULT_CAPTION_PRESET)
    if preset not in CAPTION_PRESETS:
        preset = DEFAULT_CAPTION_PRESET

    raw_background = scene.get("backgroundImage")
    raw_background = raw_background if isinstance(raw_background, dict) else {}
    background = Background(
        color=_color(scene.get("background"), DEFAULT_BACKGROUND),
        media_id=str(raw_background["mediaId"]) if raw_background.get("mediaId") else None,
        blur=_clamp(_number(raw_background.get("blur"), 18.0), 0.0, 60.0),
        dim=_clamp(_number(raw_background.get("dim"), 0.35), 0.0, 0.95),
        fit="contain" if raw_background.get("fit") == "contain" else "cover",
    )

    return Scene(
        background=_color(scene.get("background"), DEFAULT_BACKGROUND),
        accent=_color(scene.get("accent"), DEFAULT_ACCENT),
        # `false` turns the peak accent off; anything else is a colour.
        peak_accent=(
            None
            if scene.get("peakAccent") is False
            else _color(scene.get("peakAccent"), BRAND["gold"])
        ),
        wave_style=style,
        wave_scale=scale,
        caption_preset=preset,
        caption_color=_color(scene.get("captionColor"), DEFAULT_CAPTION_COLOR),
        word_highlight=scene.get("wordHighlight") is not False,
        # Bounded to a second either way: beyond that the captions belong to a
        # different sentence, which is not a timing problem any more.
        caption_offset=_clamp(_number(scene.get("captionOffset"), 0.0), -1.0, 1.0),
        # Bounded rather than trusted: a gain of +40dB or a fade longer than the
        # clip is a broken render, and a stored scene is not a promise.
        voice_gain_db=_clamp(_number(scene.get("voiceGainDb"), 0.0), -24.0, 12.0),
        fade_in=_clamp(_number(scene.get("fadeIn"), 0.0), 0.0, 10.0),
        fade_out=_clamp(_number(scene.get("fadeOut"), 0.0), 0.0, 10.0),
        background_image=background,
        layers=layers,
    )


def _layer(entry: object, clip_duration: float) -> RenderLayer | None:
    if not isinstance(entry, dict) or not entry.get("id"):
        return None
    start = max(0.0, _number(entry.get("startTime"), 0.0))
    end = _number(entry.get("endTime"), clip_duration)
    # An unset, inverted, or overlong window means "the whole clip" rather than
    # a layer that silently never appears.
    if end <= start or end > clip_duration:
        end = clip_duration
    return RenderLayer(
        id=str(entry["id"]),
        type=str(entry.get("type") or "text"),
        name=str(entry.get("name") or ""),
        text=str(entry.get("text") or ""),
        color=_color(entry.get("color"), None),
        x=_clamp(_number(entry.get("x"), 0.0), -50.0, 150.0),
        y=_clamp(_number(entry.get("y"), 0.0), -50.0, 150.0),
        width=_clamp(_number(entry.get("width"), 60.0), 1.0, 200.0),
        height=_clamp(_number(entry.get("height"), 10.0), 1.0, 200.0),
        start=min(start, clip_duration),
        end=min(end, clip_duration),
        visible=bool(entry.get("visible", True)),
        media_id=str(entry["mediaId"]) if entry.get("mediaId") else None,
        radius=_clamp(_number(entry.get("radius"), 0.0), 0.0, 0.5),
    )


def _number(value: object, fallback: float) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return fallback if number != number else number  # reject NaN


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _color(value: object, fallback: str | None) -> str | None:
    text = str(value or "").strip()
    if len(text) == 7 and text.startswith("#"):
        try:
            int(text[1:], 16)
            return text
        except ValueError:
            pass
    return fallback


def ass_color(hex_color: str, alpha: str = "00") -> str:
    """`#23a094` -> `&H00 44 A0 23` — ASS wants alpha and BGR, not RGB."""
    value = hex_color.lstrip("#")
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha}{blue}{green}{red}".upper()


def ffmpeg_color(hex_color: str) -> str:
    """`#23a094` -> `0x23a094`, the form FFmpeg's colour parsers accept."""
    return "0x" + hex_color.lstrip("#")


def showwaves_colors(hex_color: str) -> str:
    """showwaves takes bare hex without any prefix."""
    return hex_color.lstrip("#")


def escape_drawtext(text: str) -> str:
    return "".join(_DRAWTEXT_ESCAPES.get(character, character) for character in text)


# drawtext needs an explicit font file wherever fontconfig is absent or
# unconfigured. On Windows it is always absent, and asking libfreetype to find a
# default there crashes the process rather than reporting an error, so the file
# is resolved up front and text layers are skipped if nothing is found.
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/verdana.ttf",
)


def find_font_file() -> "Path | None":
    from pathlib import Path

    for candidate in _FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path
    return None


def enable_expression(start: float, end: float, clip_duration: float) -> str | None:
    """A `between(t,…)` guard, or ``None`` when the layer spans the whole clip."""
    if start <= 0.001 and end >= clip_duration - 0.001:
        return None
    return f"between(t,{start:.3f},{end:.3f})"
