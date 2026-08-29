"""The brand as it reaches the render.

The exported clip is the product — it is what an audience actually sees — so
the palette has to survive the whole way to the pixels, not just the editor
chrome.
"""

from __future__ import annotations

from app.services.jobs import _dimensions, _write_ass
from app.services.scene import (
    BRAND,
    CAPTION_PRESETS,
    DEFAULT_ACCENT,
    DEFAULT_BACKGROUND,
    PEAK_SHARE,
    ass_color,
)
from app.services.scene import parse as parse_scene


# --------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------


def test_a_new_project_already_looks_like_the_brand():
    """Nobody should have to reach for a colour picker to get the house style."""
    parsed = parse_scene({}, 10.0)
    assert parsed.background == BRAND["obsidian"]
    assert parsed.accent == BRAND["blue"]
    assert parsed.peak_accent == BRAND["gold"]


def test_the_defaults_are_the_brand_colours():
    assert DEFAULT_BACKGROUND == BRAND["obsidian"]
    assert DEFAULT_ACCENT == BRAND["blue"]


def test_a_stored_scene_still_wins_over_the_brand():
    parsed = parse_scene({"background": "#ffffff", "accent": "#ff0000"}, 10.0)
    assert parsed.background == "#ffffff"
    assert parsed.accent == "#ff0000"


def test_the_peak_accent_can_be_turned_off():
    assert parse_scene({"peakAccent": False}, 10.0).peak_accent is None


def test_the_peak_accent_can_be_recoloured():
    assert parse_scene({"peakAccent": "#00ff00"}, 10.0).peak_accent == "#00ff00"


def test_a_nonsense_peak_accent_falls_back_to_gold():
    assert parse_scene({"peakAccent": "chartreuse"}, 10.0).peak_accent == BRAND["gold"]


# --------------------------------------------------------------------------
# Gold peaks in the waveform
# --------------------------------------------------------------------------


def wave_chain(peaks: list[float], scene: dict | None = None) -> str:
    from app.services.jobs import _envelope_wave

    parsed = parse_scene(
        {**(scene or {}), "waveStyle": "envelope",
         "layers": [{"id": "w", "type": "waveform", "x": 5, "y": 40,
                     "width": 90, "height": 12}]},
        10.0,
    )
    chains: list[str] = []
    _envelope_wave(chains, "[v]", parsed, parsed.waveform_layer(), 1080, 1920, 10.0, peaks)
    return chains[0]


def test_the_loudest_bars_are_drawn_in_gold():
    # One clear peak among quiet bars.
    peaks = [0.2] * 40 + [1.0] + [0.2] * 40
    chain = wave_chain(peaks)
    assert "0xD4AF37" in chain, "no gold bar was drawn at the peak"
    assert "0x89CFF0" in chain, "the ordinary bars are not the accent colour"


def test_a_constant_tone_gets_no_gold_at_all():
    """A flat signal has no peaks, so it must not get an arbitrary gold block.

    This is what a test tone looks like, and it is what caught the first
    implementation: every bar tied for loudest, so every bar was a "peak" and
    the whole waveform rendered gold.
    """
    chain = wave_chain([0.5] * 80)
    assert "drawbox" in chain
    assert "0xD4AF37" not in chain


def test_compressed_audio_does_not_turn_the_waveform_gold():
    """Podcast audio is heavily compressed — most bars sit near the maximum.

    A threshold against the clip's loudest moment lit up nearly all of them.
    Ranking keeps the accent sparse whatever the dynamics.
    """
    import random

    random.seed(7)
    squashed = [random.uniform(0.86, 0.98) for _ in range(80)]
    chain = wave_chain(squashed)
    played = [part for part in chain.split(",") if "@1.0" in part]
    gold = [part for part in played if "0xD4AF37" in part]
    assert gold, "compressed audio should still show some peaks"
    assert len(gold) / len(played) < 0.4, (
        f"{len(gold)} of {len(played)} bars went gold — that is a wall, not an accent"
    )


def test_the_peak_share_is_a_small_slice():
    assert 0.0 < PEAK_SHARE < 0.3


def test_turning_the_peak_accent_off_leaves_one_colour():
    chain = wave_chain([0.2] * 40 + [1.0] + [0.2] * 40, {"peakAccent": False})
    assert "0xD4AF37" not in chain
    assert "0x89CFF0" in chain


def test_the_unplayed_track_stays_a_single_colour():
    """A two-tone ghost at 30% opacity reads as noise, not as unplayed audio."""
    chain = wave_chain([0.2] * 40 + [1.0] + [0.2] * 40)
    dimmed = [part for part in chain.split(",") if "@0.30" in part]
    assert dimmed, "no dimmed track was drawn"
    assert all("0x89CFF0" in part for part in dimmed)


# --------------------------------------------------------------------------
# The Kinder caption preset
# --------------------------------------------------------------------------


def style_line(path) -> list[str]:
    line = next(
        row for row in path.read_text(encoding="utf-8").splitlines()
        if row.startswith("Style:")
    )
    return line.split(",")


def test_the_kinder_preset_puts_dark_type_on_a_baby_blue_plate(tmp_path):
    parsed = parse_scene({"captionPreset": "kinder"}, 10.0)
    path = tmp_path / "kinder.ass"
    _write_ass(path, [{"start": 0, "end": 2, "text": "hook"}], "9:16", parsed)
    fields = style_line(path)

    primary, outline = fields[3], fields[5]
    # In BorderStyle 3 libass fills the plate with the outline colour.
    assert fields[15] == "3", "the plate is not enabled"
    assert outline == ass_color(BRAND["blue"]), "the plate is not baby blue"
    assert primary == ass_color(BRAND["obsidian"]), "the type is not obsidian"


def test_the_kinder_plate_shadow_matches_the_plate(tmp_path):
    """A black shadow under a coloured plate shows as a fringe at the edge."""
    parsed = parse_scene({"captionPreset": "kinder"}, 10.0)
    path = tmp_path / "kinder.ass"
    _write_ass(path, [{"start": 0, "end": 2, "text": "hook"}], "9:16", parsed)
    fields = style_line(path)
    assert fields[6] == ass_color(BRAND["blue"])


def test_the_other_presets_are_unchanged_by_the_plate_branch(tmp_path):
    """Only presets carrying a `plate` key take the inverted path."""
    for name in ("social", "boxed", "shout", "clean"):
        parsed = parse_scene({"captionPreset": name}, 10.0)
        path = tmp_path / f"{name}.ass"
        _write_ass(path, [{"start": 0, "end": 2, "text": "hook"}], "9:16", parsed)
        fields = style_line(path)
        # White type, black outline — the arrangement they always had.
        assert fields[3] == ass_color("#F8FAFC"), name
        assert fields[5] == ass_color("#000000"), name


def test_the_kinder_preset_still_clears_the_platform_band(tmp_path):
    parsed = parse_scene({"captionPreset": "kinder"}, 10.0)
    path = tmp_path / "kinder.ass"
    _write_ass(path, [{"start": 0, "end": 2, "text": "hook"}], "9:16", parsed)
    _, height = _dimensions("9:16")
    assert int(style_line(path)[-2]) / height > 0.25


def test_the_kinder_preset_gets_a_sane_caption_budget():
    from app.services.scene import caption_char_budget

    budget = caption_char_budget("kinder")
    assert 12 < budget < 40, budget
    # It sits between the shouty preset and the understated one.
    assert caption_char_budget("shout") < budget < caption_char_budget("clean")


def test_every_preset_including_kinder_has_a_label():
    for name, preset in CAPTION_PRESETS.items():
        assert preset["label"], name


# --------------------------------------------------------------------------
# Layout: captions and waveform must not occupy the same band
# --------------------------------------------------------------------------


def caption_band(preset_name: str, aspect_ratio: str, tmp_path) -> tuple[float, float]:
    """Where the burned-in caption block sits, as a share of frame height.

    Captions are positioned by the ASS style's vertical margin from the bottom,
    not by the caption layer's geometry, so this reads the margin the renderer
    actually writes rather than trusting the scene.
    """
    parsed = parse_scene({"captionPreset": preset_name}, 10.0)
    path = tmp_path / f"{preset_name}-{aspect_ratio.replace(':', 'x')}.ass"
    _write_ass(path, [{"start": 0, "end": 2, "text": "two words here"}], aspect_ratio, parsed)
    fields = style_line(path)
    width, height = _dimensions(aspect_ratio)
    font_size = int(fields[2])
    margin_v = int(fields[-2])
    # Two lines is the realistic worst case for a social caption.
    block = font_size * 2 * 1.2
    bottom = height - margin_v
    return ((bottom - block) / height, bottom / height)


# Mirrors WAVEFORM_PLACEMENT in frontend/src/App.tsx: the frontend owns the
# default layout, and these assertions are what stop it drifting into the
# captions the renderer draws.
WAVEFORM_PLACEMENT = {
    "9:16": (0.71, 0.09),
    "4:5": (0.71, 0.09),
    "1:1": (0.71, 0.09),
    "16:9": (0.80, 0.12),
}


def test_the_default_waveform_does_not_sit_in_the_caption_band(tmp_path):
    """The default layout drew the waveform straight through the captions.

    Nothing caught it because no test compared the two, and an outlined caption
    half-hides the overlap. The Kinder plate is opaque, which made it obvious.
    """
    wave_top, _ = WAVEFORM_PLACEMENT["9:16"]

    for preset in CAPTION_PRESETS:
        top, bottom = caption_band(preset, "9:16", tmp_path)
        assert bottom <= wave_top + 0.005, (
            f"{preset}: captions reach {bottom:.2f} of the frame, "
            f"overlapping the waveform that starts at {wave_top:.2f}"
        )
        assert top > 0, preset


def test_no_preset_collides_with_the_waveform_in_any_shape(tmp_path):
    """The margin is a share of height, which differs per ratio.

    Checking only the vertical shape would have missed a preset that fits at
    1920 tall and collides at 1080.
    """
    for aspect_ratio, (wave_top, _) in WAVEFORM_PLACEMENT.items():
        for preset in CAPTION_PRESETS:
            _, bottom = caption_band(preset, aspect_ratio, tmp_path)
            assert bottom <= wave_top + 0.005, (
                f"{preset} at {aspect_ratio}: captions reach {bottom:.2f}, "
                f"waveform starts at {wave_top:.2f}"
            )


def test_no_preset_sits_in_the_platform_ui_band(tmp_path):
    """The bottom fifth of a vertical frame belongs to the platform's own UI."""
    for aspect_ratio in ("9:16", "4:5", "1:1"):
        for preset in CAPTION_PRESETS:
            _, bottom = caption_band(preset, aspect_ratio, tmp_path)
            assert bottom <= 0.80, f"{preset} at {aspect_ratio} reaches {bottom:.2f}"


def test_the_default_waveform_clears_the_platform_ui_band():
    """Vertical shapes only: landscape has no platform chrome to avoid."""
    for aspect_ratio in ("9:16", "4:5", "1:1"):
        top, height = WAVEFORM_PLACEMENT[aspect_ratio]
        assert top + height <= 0.80 + 0.005, aspect_ratio


def test_the_waveform_stays_inside_every_frame():
    for aspect_ratio, (top, height) in WAVEFORM_PLACEMENT.items():
        assert top + height <= 100, aspect_ratio
        assert 0 < top < 1, aspect_ratio


# --------------------------------------------------------------------------
# The whole default stack, in every shape
# --------------------------------------------------------------------------

# Mirrors LAYOUT in frontend/src/App.tsx. The frontend owns the default layout
# and the backend owns where captions land, so nothing but a test can hold the
# two together — and this has drifted twice: once when the waveform sat in the
# caption band, and once when the title sat in the waveform.
from app.services.scene import DEFAULT_LAYOUT as LAYOUT  # noqa: E402


def caption_top(preset_name: str, aspect_ratio: str, lines: int = 2) -> float:
    """The highest the caption block reaches, as a share of frame height.

    Font size is a share of *width* while the margin is a share of *height*,
    which is why the block is so much taller in a wide frame.
    """
    preset = CAPTION_PRESETS[preset_name]
    width, height = _dimensions(aspect_ratio)
    block = lines * preset["size_ratio"] * (width / height)
    return (1 - preset["margin_ratio"]) - block


def test_the_default_layout_never_overlaps_in_any_shape():
    """Artwork, title, captions and waveform must stack without colliding.

    Checked against the worst caption preset, so changing preset after creating
    a project cannot push the captions into a layer above them.
    """
    for aspect_ratio, layout in LAYOUT.items():
        ceiling = min(caption_top(name, aspect_ratio) for name in CAPTION_PRESETS)
        art_top, art_height = layout["artwork"]
        title_top, title_height = layout["title"]
        wave_top, wave_height = layout["waveform"]

        assert art_top + art_height <= title_top, f"{aspect_ratio}: artwork runs into the title"
        assert title_top + title_height <= ceiling * 100 + 0.001, (
            f"{aspect_ratio}: title ends at {title_top + title_height}%, "
            f"captions can start at {ceiling * 100:.1f}%"
        )
        for name in CAPTION_PRESETS:
            bottom = (1 - CAPTION_PRESETS[name]["margin_ratio"]) * 100
            assert bottom <= wave_top + 0.5, (
                f"{aspect_ratio}/{name}: captions end at {bottom}%, "
                f"waveform starts at {wave_top}%"
            )
        assert wave_top + wave_height <= 100, aspect_ratio


def test_every_default_layer_stays_inside_the_frame():
    for aspect_ratio, layout in LAYOUT.items():
        for name, (top, height) in layout.items():
            assert 0 <= top, f"{aspect_ratio}/{name}"
            assert top + height <= 100, f"{aspect_ratio}/{name}"


def test_vertical_layouts_keep_everything_out_of_the_platform_band():
    """Only the waveform may approach the bottom fifth, and it must stop at it."""
    for aspect_ratio in ("9:16", "4:5", "1:1"):
        wave_top, wave_height = LAYOUT[aspect_ratio]["waveform"]
        assert wave_top + wave_height <= 80.5, aspect_ratio
