# Kinder

Self-hosted podcast video and audiogram studio for local creative work on Unraid or a desktop LAN.

The repository, Docker image, and appdata paths still read
`podcast-audiogram-studio` — renaming those would orphan a running container
and its data, so they are deliberately left alone. Kinder is the product name;
that is the identifier on disk.

This first build is a working appliance foundation:

- FastAPI backend with SQLite persistence under `runtime/config`
- Kinder brand throughout: Obsidian Black canvas, Baby Blue primary, Champagne Gold peaks — in the app chrome *and* in what gets rendered (see `docs/BRAND.md`)
- React creator workspace with Home, Quick Create, Projects, Templates, Exports, and Studio Editor views
- Save a project's look as a reusable template — the design carries across episodes, the audio and artwork do not
- Cancel queued or running renders; a cancelled job stops its encoder and cleans up after itself
- Delete a project when you are done with it — its queued jobs are cancelled and its renders removed with it
- **Approval inbox**: clips cut automatically wait for you to keep or discard — nothing is ever posted
- **Watch a podcast feed**: new episodes download, transcribe and cut themselves into clips waiting for approval — nothing is ever posted automatically (`docs/FEEDS.md`)
- **Make a set of clips**: one episode becomes six clips, cut on word boundaries, on-brand, rendered in parallel and downloadable as one ZIP
- Speaker labels: tell it how many people are talking and captions tint per speaker, with editable names (`docs/SPEAKERS.md`)
- Suggested clips: the transcript and peak envelope are scored to propose the moments worth posting, each with the reasons it was picked
- Clip edges snap off the middle of a word when you let go of a handle, with an undo
- Word-by-word karaoke captions, and every export normalised to -14 LUFS
- Fair queueing, so one person's batch of ten does not block everyone else
- Per-platform upload checks for 13 destinations — length, file size, shape, codecs — before you spend the render (`docs/PLATFORMS.md`)
- Optional local language model that reads the shortlist and ranks clips by what they are about (`docs/LOCAL_MODEL.md`)
- Guided destination, source, transcript clip, and template workflow that creates the same editable Studio project
- Direct-manipulation canvas with draggable layers, safe-zone guide, layer visibility, property controls, and timeline playhead
- Background job worker for media analysis, server-side waveform peaks, local GPU transcription, and real FFmpeg audiogram rendering
- Real speech transcription with Faster-Whisper — searchable, word-timed, and driving both the clip picker and the burned-in captions
- Real waveform envelopes in the clipper and canvas, drawn from the audio rather than from a placeholder pattern
- Social-ready output: blurred cover-art background, show artwork, big burned-in captions kept clear of platform UI, and a progress bar
- Hardware encoding on NVIDIA (auto-detected), and still layers baked once instead of re-filtered every frame
- Licensed music library with genre/mood search, in-editor preview, and a music bed the renderer mixes under the voice with fades, looping, and speech ducking
- Interface sound cues, mutable per browser
- GPU inventory through `nvidia-smi` when NVIDIA devices are visible
- Docker and Unraid release files for the intended single-container deployment path

## Functional Parity Target

The product target is a clean-room, self-hosted functional replacement for
Headliner-style podcast/video workflows, not a visual clone of Headliner's
marketing pages or private application code.

Maintain the living parity contract in
[`docs/HEADLINER_PARITY_MATRIX.md`](docs/HEADLINER_PARITY_MATRIX.md). A feature
is not complete just because a control exists; it must perform the operation,
persist its result, survive refresh/restart where appropriate, feed subsequent
workflow stages, and produce the expected output.

Structural notes on how the reference application is put together - and where we
intend to differ - are in
[`docs/HEADLINER_ARCHITECTURE_NOTES.md`](docs/HEADLINER_ARCHITECTURE_NOTES.md).

## Local Development

Backend:

```powershell
cd C:\Users\mavin\Downloads\podcast-audiogram-studio\backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8080
```

Frontend:

```powershell
cd C:\Users\mavin\Downloads\podcast-audiogram-studio\frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## Database and Users

The app stores its local SQLite database in `runtime/config/app.db` by default. Tables are created automatically when the backend starts, and the first browser run shows an admin bootstrap form.

You can also initialize the database and create/reset an admin from PowerShell:

```powershell
cd C:\Users\mavin\Downloads\podcast-audiogram-studio
.\scripts\init-db.ps1
.\scripts\create-admin.ps1 -Email admin@example.com -Password "change-this-password"
```

After signing in as an admin, use the **User database** panel in the WebUI to add users, create admins, and disable or re-enable accounts.

## Audio Library

The music bed and the interface cues come from third-party packs licensed for
**use but not redistribution**, so they live in a runtime volume rather than in
this repository or the container image.

```powershell
cd C:\Users\mavin\Downloads\podcast-audiogram-studio
.\scripts\import-audio-library.ps1 -DownloadsDir C:\Users\mavin\Downloads
```

That catalogues 414 chiptune tracks across 46 genres plus eight interface cues,
searchable by title, genre, or mood. Attribution is required by both licences and
is produced automatically: every render that uses a track writes `CREDITS.txt`
beside the MP4.

Terms, the import options, and how to add another pack are in
[`docs/AUDIO_LIBRARY.md`](docs/AUDIO_LIBRARY.md). Read it before packaging a
build that includes audio.

## Transcription

Captions are the product: on a muted feed they are the only thing carrying the
clip. Faster-Whisper runs locally, on the GPU when one is usable, and the model
is cached under `/config/models` so a restart does not re-download it.

Measured on an RTX 4090 with the default `small` model: **90 seconds of podcast
audio in 5.1 s — about 15x realtime**, producing 217 word-level timings.

Word timings are what make the captions readable. Whisper returns whole
sentences, which are far too long to burn in at social sizes, so lines are split
on the word that crosses either a 42-character or 3.5-second budget — and the
split is *balanced*, so the last line is as full as the rest instead of stranding
a single word on screen for a hundredth of a second.

Model, language, and an on/off switch live in the admin panel, which also shows
which device is really doing the work — "GPU configured" and "GPU running" are
not the same claim.

For GPU transcription in the container, install the CUDA libraries alongside the
base requirements:

```
pip install -r backend/requirements.txt -r backend/requirements-gpu.txt
```

Without them the app still transcribes, on the CPU, roughly 15x slower.

## GPU Deployment

The slim image cannot use the hardware: CTranslate2 needs cuBLAS and cuDNN to
load a Whisper model on a GPU, and neither ships in `python:slim`.
`Dockerfile.gpu` starts from NVIDIA's CUDA runtime instead, which carries both.

```bash
docker compose -f docker-compose.gpu.yml up -d --build
```

Storage and the model cache are parameterised, because a server usually already
has somewhere sensible for each:

```ini
# .env beside the compose file
PAS_PORT=8099
PAS_CONFIG_HOST=/mnt/storage/appdata/podcast-audiogram-studio/config
PAS_DATA_HOST=/mnt/storage/appdata/podcast-audiogram-studio/data
# Reuse a HuggingFace cache another service already filled rather than
# downloading the same weights twice.
PAS_MODELS_HOST=/mnt/storage/appdata/whisper/hub
PAS_WHISPER_MODEL=base
```

### Updating a running deployment

Rebuild and recreate in one step:

```bash
docker compose -f docker-compose.gpu.yml up -d --build
```

`docker restart` is not enough. It restarts the *existing container*, which is
pinned to the image ID it was created from, so a rebuilt `:gpu` tag is ignored
and the old code keeps running — with no error to say so. If a change seems not
to have taken, compare the two:

```bash
docker inspect podcast-audiogram-studio --format '{{.Image}}'
docker inspect podcast-audiogram-studio:gpu --format '{{.Id}}'
```

Recreating the container is safe: config, database, and renders all live on the
bind mounts above, not in the container's writable layer.

Put `/config` and `/data` on a fast pool, not a parity-protected array: renders
move multi-gigabyte files and SQLite wants a local filesystem.

`NVIDIA_DRIVER_CAPABILITIES` must include `video`. Dropping it is the usual
reason NVENC goes missing from a container that otherwise sees the GPU.

### Two GPUs, two lanes

Assign a card to each role in the admin panel and they are used at the same
time. The job worker runs one thread per lane — `media`, `transcribe`, `render`
— and each claims only its own job kinds, so a long render no longer parks a
transcribe behind it.

Lanes are deliberately serial *within* themselves: two concurrent renders would
fight over the same encoder session and the same CPU-bound filter graph, which
is slower than doing them in order.

Claiming is a conditional `UPDATE ... WHERE status = 'queued'` rather than a
read followed by a write. With three lanes polling one table, a read-then-write
race would eventually dispatch the same job twice — running a render twice into
the same output directory.

### Publishing

`.github/workflows/release.yml` builds and pushes both images to GHCR, gated on
the full CI suite:

| Trigger | CPU tag | GPU tag |
| --- | --- | --- |
| push to `main` | `:edge` | `:gpu-edge` |
| tag `v1.2.3` | `:1.2.3`, `:latest` | `:gpu-1.2.3`, `:gpu` |

It uses the workflow's built-in `GITHUB_TOKEN`, so nothing needs configuring —
but after the first successful run, make the package public once by hand
(Packages → Package settings → Change visibility). Until then `docker pull`
requires a login, which is the usual reason an Unraid template reports "image
not found".

### Verified on real hardware

Built and run on an Unraid host (Threadripper 2990WX, Quadro RTX 5000 + RTX
4090, Docker 29.5, NVIDIA container toolkit):

| Check | Result |
| --- | --- |
| Both GPUs visible in the container | Quadro RTX 5000, RTX 4090 |
| CTranslate2 CUDA devices | 2, with float16 |
| NVENC encode | works |
| Whisper on the shared model cache | `base` reused, nothing downloaded |
| Real speech, 90 s of podcast audio | **3.1 s — 29x realtime, 219 word timings** |
| 30 s clip rendered with artwork, captions, waveform, progress bar | 11.2 s |
| API verification | 22 checks pass (attribution skipped, library not yet imported) |
| Browser smoke | 11 steps pass |
| Transcribe and render launched together | **overlapped 6.1 s**, 8.2 s wall clock vs ~14 s serialised |

The build fails rather than warns if that FFmpeg has no `h264_nvenc` compiled
in, or if the font `drawtext` needs is missing — both are things that regress
silently when a base image changes and only surface as a broken export.

## Performance

Rendering is the slowest thing the app does, so it is measured rather than
guessed. On an RTX 4090, a 30-second 1080x1920 clip with a blurred cover-art
background, show artwork, waveform, captions, progress bar and a music bed:

| Change | Time | Realtime |
| --- | --- | --- |
| Before | 12.2 s | 2.5x |
| Still layers baked once | **3.8 s** | **7.9x** |

The win is not the encoder. Profiling showed the background image alone cost
4.2 s of the original 12.2 s: `-loop 1 -i cover.png` is a *video* stream, so its
`scale,crop,boxblur,scale,eq` chain re-ran on all 900 identical frames. Baking
each still layer to a PNG once (`app/services/plates.py`) costs 0.17 s and turns
the rest into a plain composite.

NVENC is auto-detected and used when a working device is present, but on this
workload CPU and GPU encode finish within 0.1 s of each other — the remaining
time is decode and filtering, not encoding. It still earns its place on longer
and larger renders, and it keeps the CPU free for the filter graph. Override
with `PAS_VIDEO_ENCODER=cpu|nvenc|auto`.

## Using a Big Machine

A single render uses about four threads. FFmpeg's filter graph is a long serial
chain — overlay, then a drawbox per waveform bar, then subtitles, then text —
and a chain does not parallelise however many cores you give it. Measured on a
32-core host, one render peaked at 376% CPU: **under 6% of the machine.**

So the way to use a big box is more renders at once, not a wider one. The job
worker runs several threads per lane, sized from the core count:

| Lane | Workers on 32 cores | Why |
| --- | --- | --- |
| `render` | 4 | Bounded by NVENC sessions as much as by cores; consumer cards cap concurrent encodes and exceeding it fails the job. |
| `media` | 4 | Probing and waveform extraction are short and CPU-bound. |
| `transcribe` | 1 | A second worker is a second model resident in VRAM, and both would serialise on the same GPU anyway. |

Override with `PAS_RENDER_WORKERS`, `PAS_MEDIA_WORKERS`,
`PAS_TRANSCRIBE_WORKERS`, or cap FFmpeg itself with `PAS_FFMPEG_THREADS` —
useful when several renders share a box, since four jobs each grabbing every
core is worse than four taking a slice.

Measured on the Unraid host, six 12-second clips from one episode:

| | |
| --- | --- |
| Serial (one render lane) | ~27 s |
| Four render lanes | **9.5 s** — 72 s of finished video, all six distinct |

### Renders are isolated

Each render works in its own directory and publishes at the end, and two
renders of the same project serialise on a per-project lock. Both matter once
renders run concurrently: they previously shared a scratch directory *and* an
output path, so several FFmpeg processes wrote one `audiogram.mp4` while each
read whichever `captions.ass` won the last write. It was luck that the result
was ever coherent.

## One Clip, Every Platform

A clip usually needs to go to more than one place. Pick the shapes in the
Studio and the app copies the project and renders each one:

| Shape | For |
| --- | --- |
| 9:16 | TikTok, Reels, Shorts |
| 4:5 | Instagram feed |
| 1:1 | Feed posts |
| 16:9 | YouTube, X |

A variant is a real project rather than a render setting, because the layouts
genuinely differ — a title that spans a vertical frame is lost in a wide one.
Geometry carries over by preserving each layer's **pixel** size and centre, not
its percentages: a waveform 9% tall in a 1920px frame is 173px, and the same 9%
in a 1080px square is 97px, which reads as a different design. Three variants
render in about 5 s, because they go through separate render lanes.

Two things this surfaced, both now fixed:

- **Caption size came from frame height**, so the same clip rendered 9:16 and
  16:9 got 100px and 37px captions. Reading size tracks how wide a line can
  run, so it comes from width now and every shape reads the same.
- **The line splitter and the burned-in font disagreed** — lines were cut at a
  flat 42 characters while only about 18 fit, so libass re-wrapped each into
  three and the block collided with the waveform. The budget is now derived
  from the preset's font size.

## Verifying an Instance

Three layers, because each catches what the others cannot.

**Unit and API** — 83 tests over scene parsing, filter graphs, caption
splitting, peak resampling, licensing, and additive migrations:

```powershell
cd backend
.\.venv\Scripts\python -m pytest tests -q
```

**Connection verification** — 25 checks over every HTTP path a real session
uses: bootstrap, sign-in, upload, background jobs, waveform peaks, project edit,
sound library, render, download, attribution, path-traversal refusal, and a
check that no background job failed. No dependencies, so it works against a dev
server or a running container:

```powershell
python scripts\verify.py --base-url http://localhost:8080
```

**Browser smoke** — drives the real interface and fails on any console error,
any view that renders nothing, or a broken Quick Create walkthrough:

```powershell
cd frontend
npm run smoke -- --base-url http://localhost:8080
```

That third layer earns its place: the other two cannot see a React crash. A
component that throws still returns HTTP 200 with a valid bundle, and the page
simply renders black — which is exactly what a temporal-dead-zone reference in
the Studio did while every backend test stayed green.

CI builds the image, boots it, and runs all three against the container.

## Production Container

```powershell
cd C:\Users\mavin\Downloads\podcast-audiogram-studio
docker build -t podcast-audiogram-studio:local .
docker run --rm -p 8080:8080 -v ${PWD}\runtime\config:/config -v ${PWD}\runtime\data:/data podcast-audiogram-studio:local
```

The image is self-contained: FFmpeg, a font for burned-in text, the Python
runtime, and the built frontend served by the same process on port 8080. It
runs as an unprivileged user (uid 568, matching Unraid), declares a healthcheck
against `/api/health`, and uses `tini` so `docker stop` is a clean shutdown
rather than a ten-second wait. Dependencies are pinned and installed with
`npm ci` and a locked `requirements.txt`, so the build is reproducible.

For Unraid with the NVIDIA runtime, pass GPU capabilities through and mount
`/config` and `/data` to persistent shares. The sound library lives under
`/data/library`, so it persists with the data share and never enters the image. The sound library lives under `/data/library`, so it persists with the data share and never enters the image.

## Current Scope and Limitations

The core local workflow is functional, but this is not yet full Headliner/Adobe parity. Current limitations are:

- The renderer honours the scene's background, waveform layer, and text layers with their in/out times; image and artwork layers are still preview-only.
- SQLite tables are created automatically and additive columns are applied at startup; renames, drops, and type changes would still need a real migration tool. Richer RSS persistence and brand kits remain follow-up work.
- Music ducking uses a sidechain compressor, so the dip control is a calibration rather than an exact dB reduction; per-track volume automation and timeline sound effects are not implemented.
- A clip whose range runs past the end of its source is trimmed to the source and the render says so; one that starts past the end fails rather than producing a silent frozen frame.
- Scene layers still use a flat array with float-second timing; the migration to an id-keyed map with integer milliseconds is specified in the architecture notes.
- The clip selector zooms by re-fetching a windowed peaks range, so detail improves rather than stretching. The Studio timeline still has no zoom or horizontal scroll.
- The container has not been built and booted on this machine, because no Docker runtime is installed here. The image contract is exercised locally instead (same environment variables, same paths, frontend served from `dist` by the backend), and CI builds, boots, and verifies the real image on every push.

The app does not require a cloud AI API. The existing GPU panel stores UUID-based assignments and reports visible NVIDIA devices; the local development renderer remains CPU-first until the NVIDIA production image is enabled.

