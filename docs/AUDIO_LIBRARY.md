# Audio Library

Kinder can mix a music bed under a clip and play its own
interface cues. Both come from third-party packs that this installation is
licensed to **use** but not to **redistribute**, so the audio lives in a runtime
volume rather than in this repository or the container image.

## The rule that shapes the design

| Pack | Licence | Commercial use | Credit | Redistribution |
| --- | --- | --- | --- | --- |
| Audio Asset Archive: Mega Music Multipack (414 chiptune tracks, © John Aaron Coulter / Aibioweapon) | CC BY + royalty-free hybrid, v1.0 | Yes — podcasts and synced media named explicitly, worldwide and perpetual | **Required**: "Audio by Aibioweapon" | **Forbidden** as standalone files or as part of another library or collection |
| JDSherbert – Ultimate UI SFX Pack (FREE), © 2023 JDSherbert | JDSherbert product licence | Yes — commercial and non-commercial projects | **Required**: "UI sounds by JDSherbert" | **Forbidden**; the licence also forbids modifying the files |

Two consequences are baked into the code:

- **The library is never repository or image content.** It lives under
  `PAS_LIBRARY_DIR` (default `runtime/data/library`), which `.gitignore` and
  `.dockerignore` both exclude. Shipping the packs inside a distributable build
  would be redistributing them as part of another collection.
- **Effect files are copied verbatim, never transcoded.** The JDSherbert licence
  forbids modification, and the pack already ships `wav`, `mp3`, `ogg`, and
  `m4a`, so the importer picks one of those rather than converting anything. The
  music licence *does* permit adaptation, which is why trimming, looping, fading,
  and level changes are fair game for the bed.

Attribution is enforced by the pipeline, not by memory: any render that uses a
track writes `CREDITS.txt` beside the MP4 and repeats the credits in
`render-manifest.json`.

## Importing

```powershell
cd C:\Users\mavin\Downloads\podcast-audiogram-studio
.\scripts\import-audio-library.ps1 -DownloadsDir C:\Users\mavin\Downloads
```

Or call the importer directly for control over which volumes go in:

```powershell
cd backend
.\.venv\Scripts\python -m app.cli.import_library `
  --music-dir "C:\Users\mavin\Downloads\Audio_Asset_Archive_WAVs_1-100" `
  --music-dir "C:\Users\mavin\Downloads\Audio_Asset_Archive_WAVs_101-200" `
  --song-index "C:\Users\mavin\Downloads\Audio_Asset_Archive_SONG_INDEX.rtf" `
  --sfx-dir "C:\Users\mavin\Downloads\JDSherbert - Ultimate UI SFX Pack (FREE)" `
  --probe-durations
```

The importer:

1. copies audio into `<library>/music/<pack>/` and `<library>/sfx/<pack>/`,
   skipping macOS resource forks and `.DS_Store`;
2. parses `Audio_Asset_Archive_SONG_INDEX.rtf` for titles, composers, genres,
   durations, loop structure, and search tags, and writes them to `pack.json`;
3. rebuilds the `sound_assets` table from what is actually on disk.

`--sync-only` re-reads the library without copying, which is what you want after
deleting a track by hand. `--probe-durations` runs `ffprobe` over anything the
song index does not cover (the twelve intro/loop split pairs and the effects).

Everything in the database is derived state — deleting `sound_assets` and
re-running the sync is always safe.

### What lands in the catalogue

414 indexed tracks across 46 genres, 438 music files (twelve tracks ship as
separate `_Intro`/`_Loop` halves alongside the combined version), and 8 interface
cues mapped to roles: `select`, `confirm`, `cursor`, `cancel`, `error`, `open`,
`close`, `swipe`.

Tracks the index marks "Does Not Repeat" — and the intro half of any intro/loop
pair — are flagged unloopable, so the editor offers padding rather than a
restart that would be audible.

## Using a music bed

In Studio, the inspector's **Music bed** panel searches by title, genre, or mood
tag and previews a track before committing to it. Choosing one writes
`scene.music` (see [`HEADLINER_ARCHITECTURE_NOTES.md`](HEADLINER_ARCHITECTURE_NOTES.md#3-not-everything-on-the-timeline-is-a-canvas-layer)
for why the bed is not a canvas layer), and the render mixes it in:

- **Level** sets the bed's gain, defaulting to −18 dB.
- **Dip under speech** ducks the bed with a sidechain compressor keyed off the
  voice track. This is a calibration rather than an exact conversion — the actual
  reduction depends on how far the voice exceeds the threshold — so treat the dB
  figure as a dial, not a guarantee. Set it to 0 to switch ducking off entirely.
- **Fades** are clamped to half the clip, so a 2-second fade cannot swallow a
  3-second clip.
- **Loop** repeats a short track to fill the clip; with it off, the bed is padded
  with silence instead.

The waveform visual always follows the voice, never the bed.

## Interface cues

The header's speaker button mutes and unmutes cues; the preference is stored per
browser in `localStorage`. Cues fail silent — a library that was never imported,
a browser that blocks autoplay, and a muted user all produce the same result.

## Adding another pack

Add a `PackLicense` entry to `PACKS` in `backend/app/services/library.py`
recording the licence name, the required attribution string, and whether
redistribution is permitted. `redistributable` is not decorative: it is the flag
that says whether the files could ever be shipped inside a build. Everything
downstream — the catalogue, the credits file, the packs endpoint — reads from
that entry.
