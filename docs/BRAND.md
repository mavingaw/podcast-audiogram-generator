# Kinder brand

The palette, marks, and render defaults come from the Kinder asset bundle
(`kinderaudiogramassetbundle.zip`, supplied 2026-08-28). Unlike the audio packs
in `docs/AUDIO_LIBRARY.md`, these are Kinder's own brand assets, so they ship in
the repository and in the container image.

## Palette

| Token | Name | Hex | Where it lands |
|---|---|---|---|
| base | Obsidian Black | `#0B0D11` | App canvas, and the default audiogram background |
| surface | Jet Slate | `#161B22` | Panels, cards, inspector |
| border | Charcoal Line | `#262C36` | Frame and track borders |
| primary | Baby Blue | `#89CFF0` | Primary buttons, active nav, the waveform, the caption plate |
| primary-light | Ice Glow | `#D4EEFC` | Hover states |
| accent | Champagne Gold | `#D4AF37` | Waveform peaks, progress, premium marks |
| accent-light | Warm Ochre | `#F3E5AB` | Metallic gradient stops |
| text | Pure Off-White | `#F8FAFC` | Titles and captions |
| text-muted | Cool Steel | `#94A3B8` | Timestamps, inactive labels |

Defined once per side and inherited everywhere else:

- **Backend** — `BRAND` in `backend/app/services/scene.py`. This is authoritative:
  it decides what a rendered clip actually looks like.
- **Frontend** — `BRAND` in `frontend/src/api.ts`, mirroring the backend, plus
  the `--n-*`, `--accent-*` and `--warm-*` ramps in `styles.css`. Every semantic
  token (`--bg`, `--surface`, `--accent`, …) resolves through those ramps, so
  the palette is changed in one place, not per component.

A stored scene always wins over the defaults: rebranding does not repaint
projects somebody already coloured by hand.

## Marks

In `frontend/public/brand/`, served as static files rather than inlined so they
stay editable:

| File | Used for |
|---|---|
| `kinder-logo-horizontal.svg` | Sidebar lockup |
| `kinder-logo-stacked.svg` | Sign-in splash |
| `kinder-icon.svg` | Apple touch icon, PWA icon |
| `kinder-sound-icon.svg` | Spare equalizer mark |
| `kinder-ui-glyphs.svg` | Ratio and feature glyph sprite |
| `../favicon.svg` | Browser tab |

`public/manifest.json` makes the app installable. Its icon paths were repointed
from the bundle's `/assets/` to `/brand/`, and the orientation lock was dropped:
Studio is a wide two-panel editor, and `portrait-primary` would fight the view
people spend all their time in.

## How the brand reaches the render

The exported clip is the product, so the palette has to survive to the pixels.

- **Scene defaults** — a new project starts obsidian with a baby-blue waveform.
- **Gold peaks** — the loudest bars in the waveform are drawn in Champagne Gold,
  chosen per bar when the filter graph is built, so it costs no extra filters.
  See `_peak_bars` in `backend/app/services/jobs.py` for why this ranks bars
  rather than thresholding them.
- **The `kinder` caption preset** — obsidian type on a baby-blue plate. In ASS
  `BorderStyle 3` libass fills the plate with the *outline* colour, which is why
  a coloured plate is set there and not in `BackColour`.

Verified on a real encode rather than by inspecting the filter graph: a rendered
frame sampled at the pixel level contained 1.97M obsidian, 62k baby blue and 32k
gold pixels.

## Two defects this integration exposed

Both were found by looking at a rendered frame, which nothing else had done.

1. **A wall of gold.** Peaks were first selected by thresholding against the
   clip's loudest moment. Podcast audio is heavily compressed, so most bars sit
   near the maximum and nearly all of them qualified. Now the top `PEAK_SHARE`
   of bars are ranked, and a peak must also rise above one of its neighbours —
   so a constant tone gets no gold at all instead of an arbitrary gold block.

2. **Captions drawn through the waveform.** The default waveform sat at 62–74%
   of the frame while captions occupied roughly 59–70%. An outlined caption
   half-hid the collision; the opaque plate made it obvious. The waveform moved
   to 71–80% — below the captions, above the platform UI band — and the `boxed`
   and `kinder` caption margins were raised clear of it.
   `test_the_default_waveform_does_not_sit_in_the_caption_band` pins this.

3. **`clean` captions in the platform band.** Measured across all four ratios,
   `clean` at a 0.10 margin overlapped the waveform in *every* shape and sat
   inside the platform band on all three vertical ones — so it was broken, not
   deliberately low. What makes it understated is its size and its lack of a
   plate, not its position, so it now clears both like the rest.

4. **The title drawn through the waveform.** Moving the waveform down to 71%
   put it under the title at 77%. Fixed by the per-shape stack below.

5. **The editor showed captions in the wrong place.** The canvas positioned the
   caption layer from its stored geometry — 88% of the frame — while the
   renderer ignored that and placed captions from the preset's margin, around
   65%. The editor was drawing captions inside the platform-UI guide it draws
   itself, while the export put them somewhere else entirely. The canvas now
   derives the caption band from the preset, so preview and export agree by
   construction.

6. **The canvas ignored scene changes.** Studio's layer state only re-synced on
   project id, title or clip times, so anything that rewrote layers from outside
   the canvas — applying a template, or the fix button below — saved correctly
   and left the canvas showing the old layout. It now watches the serialised
   layers, which is stable enough not to clobber a drag in progress.

## The default stack

One layout cannot serve every ratio. Captions are sized from frame *width* but
positioned by a margin in frame *height*, so the band they occupy grows from
44% of a 9:16 frame to 69% of a 16:9 one. Each shape therefore gets its own
stack — artwork, title, caption band, waveform — defined by `LAYOUT` in
`frontend/src/App.tsx` and checked against the renderer by
`test_the_default_layout_never_overlaps_in_any_shape`. That test compares
against the *worst* caption preset, so changing preset after creating a project
cannot push captions into the layer above.

Variants get the same guarantee from a different direction. Remapping preserves
a layer's pixel size and centre, which is right for artwork but slides the
caption band underneath a waveform that stayed put — every variant of a default
project used to collide. `settle_waveform` in `backend/app/services/variants.py`
pushes the waveform clear after any shape change, and shrinks it rather than let
it run into the platform's interface.

## Projects made before the fix

Existing scenes are left alone: silently relayouting somebody's design is worse
than the collision. Instead the Design panel warns when a layer sits where the
captions will be burned in, and offers to move it below them. Of the fifteen
real projects on this instance, one was affected.

The notice lives in the panel's Captions group, which can be below the fold —
worth moving somewhere more prominent if it turns out to matter.
