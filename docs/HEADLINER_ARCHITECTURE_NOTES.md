# Headliner Architecture Notes (Clean-Room)

What the reference application appears to do *structurally*, and what Podcast
Audiogram Studio should do about it.

These are observations about public service names, route shapes, and state
vocabulary — the kind of facts you can read off any web app's network tab. They
exist so we can design an equivalent system on our own terms.

**Nothing in this document is copied implementation.** No Headliner JavaScript,
CSS, image, font, template, or private API payload has been ported into this
project, and none may be. Where a note describes a mechanism, we re-derive it;
where it names a concept, we name our own. Source: the local HTTrack capture
described in [`HEADLINER_LOCAL_CAPTURE_NOTES.md`](HEADLINER_LOCAL_CAPTURE_NOTES.md),
webapp `6.33.0`.

---

## 1. Service decomposition

The web client talks to roughly twenty separate hosts, each one a single
responsibility:

| Concern | Reference service | Our equivalent |
| --- | --- | --- |
| Sign-in, tokens, password reset | `authentication-service` | `app.services.auth` + `/api/auth/*` |
| User records | `user-service`, `headliner-user-service` | `User` model + `/api/users` |
| Upload intake | `media-upload-service`, `recording-upload-service` | `app.services.storage` + `/api/media/upload` |
| Probe/metadata | `audiogram-data-service` | `app.services.media.ffprobe_media` |
| Transcription | `transcript-service` | `transcribe` job (Faster-Whisper pending) |
| Project documents | `video-project-mgmt-service` (v1 and v3) | `Project.scene_json` + `/api/projects` |
| Render/compose | `creation-service` | `render` job + FFmpeg |
| Stock imagery and generation | `image-service`, `image-search-service` | not planned; local media only |
| Topic and summary extraction | `keyword-extractor-service` | future AI clip selection |
| Podcast feeds | `podcast-service` (v1 and v2) | `app.services.rss` |
| Playback embeds | `embed-service`, `embed-video-service` | not planned; we export files |
| Analytics | `event-ping-service`, Mixpanel, Branch, Facebook | none, deliberately |

**What we take from this.** The decomposition is a deployment choice, not a
design requirement — a self-hosted appliance should stay one process. But the
*seams* are worth copying, because they mark where work is asynchronous. Upload,
probe, transcribe, and render are separate services there for the same reason
they are separate `Job` rows here: each can fail, retry, and report progress on
its own.

The seam we have not yet honoured is transcription. Ours is a synthetic fixture
inside the job worker; theirs is a service the client polls independently of the
project. Keeping that boundary is what will let a Faster-Whisper run on the 4090
proceed while an NVENC render occupies the RTX 5000.

## 2. The project document is normalised, not a layer list

The client assembles a project configuration whose top-level members are, by
observation: `slideshowInfo`, `soundwave`, `textOverlayInfo`, `versionInfo`,
`watermark`, `layerOrder`, `mainMediaContainer`, `progress`, and `timer`, joined
elsewhere by `captions`, `videoClips`, `edgeVideos`, `aspectRatio`, and
`dimensions`.

Three properties matter:

1. **Entities live in id-keyed maps** (`textOverlayById`, `watermarkById`), not
   in an array. Editing one element does not rewrite the document.
2. **Stacking is a separate ordered list** (`layerOrder`). Z-order is data, not
   array position.
3. **Time is integer milliseconds** throughout (`startMillis`, `endMillis`),
   including for text overlays and the waveform.

Tracks are created with a type and a placement policy — the observed values are
`grouped` and `free`. Grouped tracks (captions, media) slot next to their
siblings; free tracks (a one-off text element, the waveform) are positioned
independently.

**What we take from this.** Our `scene_json` is currently a flat `layers` array
where index *is* z-order and times are floating-point seconds. That is fine at
today's size and wrong at tomorrow's: reordering rewrites every layer, and float
seconds accumulate drift once caption boundaries are edited by hand.

The migration to make, in order:

- move layer timing to integer milliseconds, matching the caption formats we
  already emit;
- split `layers: []` into `layersById: {}` plus `layerOrder: []`;
- give each layer a placement policy so "add a caption" lands next to existing
  captions rather than on top of the stack.

This is tracked as `PARTIAL` under *Track order controls stacking* and *Element
start/end times* in the parity matrix.

## 3. Not everything on the timeline is a canvas layer

The waveform is described by `soundwaveOptions`/`waveformPrefs` and converted
between placement and dimensions by dedicated helpers; captions carry their own
`captionsConfig` and a media source (`assetType`/`assetId`); the progress bar and
timer are separate top-level members. These are *not* generic layers that happen
to draw a waveform — they are distinct kinds with their own settings.

**What we take from this, and what we already did.** The music bed added in this
change follows the same rule. It is a single `scene.music` object, not a layer:
it has no position, no size, and no z-order, and it always spans the clip. Giving
it a canvas layer would have meant inventing coordinates nobody can use.

```jsonc
"music": {
  "soundId": "…",
  "gainDb": -18,          // level under the voice
  "duckDb": -12,          // how far it dips while someone is speaking
  "fadeInSeconds": 1,
  "fadeOutSeconds": 2,
  "startOffsetSeconds": 0,
  "loop": true
}
```

The same treatment is the right shape for the progress bar and a watermark when
we build them.

## 3a. The waveform is generated content with a lifecycle

The client tracks `waveformStatus` and polls it until `completed`, treating
`error`/`errorAck` as a terminal failure ("Error loading audio waveform"). Peaks
are not computed in the browser from the audio file — they are produced once,
server side, and referenced by a `waveformPrefId`. Style is separate from data:
`waveType` (observed values `none`, `roundBars`, `wideRoundBars`), a colour, and
a `wavePosition`.

**What we take from this, and what we already did.** Our editor drew
`18 + ((i * 31) % 68)` — a fixed sawtooth with no relationship to the audio, in
both the clipper and the canvas preview. Choosing a clip boundary was guesswork.

We now match the reference model:

- a `waveform` job decodes the upload with FFmpeg and stores a peak envelope on
  the media asset (`app/services/waveform.py`);
- `GET /api/media/{id}/peaks` reduces that envelope to the bucket count the
  caller can draw, optionally windowed to a time range, so zooming into a clip
  costs no re-decode;
- the endpoint answers `ready: false` rather than 404 while the job runs, and the
  editor polls;
- style is data, not a constant: `waveStyle` and `waveScale` live in the scene.

The envelope is stored at 10 buckets per second as base64 bytes — about 48 KB
for a one-hour episode, against roughly 250 KB for the same data as JSON floats.

## 4. Asynchronous work is polled, and every stage has a status

The status vocabulary across the bundle is consistent: `queued`, `pending`,
`processing`, `ready`, `completed`, `error`, `failed`, plus a domain-specific
`transcribing`. Jobs are polled (`pollForAsset`), and image generation and
video-clip creation are modelled as job resources in their own right
(`text-to-image-job`, `image-to-video-job`).

**What we take from this.** Our `JobStatus` enum already covers this ground and
our `/api/jobs/{id}/events` stream is better than polling. Coverage has grown —
waveform generation is now a job alongside `analyze_media`, `transcribe`,
`render`, and `model_download` — but clip analysis and template application will
need to be jobs too, rather than synchronous handlers, the moment they do real
work.

Two things about polling that the reference gets right and we had wrong:

- **Poll only while something is running.** We refreshed on a fixed 2.5 s
  interval forever, in a hidden tab, with requests that could overlap their own
  slow responses. That exhausted the browser's socket pool
  (`net::ERR_NO_BUFFER_SPACE` in the console logs). The loop now waits for each
  round trip, pauses when the tab is hidden, and drops to a 15 s heartbeat when
  no job is active.
- **A stream needs an end.** Our SSE endpoint looped forever, holding a worker
  thread and a socket for the life of the page. It now stops when the job
  finishes or after fifteen minutes, and sends comment frames instead of
  repeating an unchanged payload every second.

## 5. The guided flow is a wizard with named steps

Observed step identifiers include `objective`, `Source`, `Language`,
`videoUpload`, `entireVideoWaveform`, `ClipAudioStep`, `createProject`, and
`ReturnToClipAudioStep`. Two things follow. The destination/objective is chosen
*first*, before any media exists, so it can constrain aspect ratio and length
downstream. And the clip step is re-enterable — the flow explicitly supports
coming back to it after moving on.

**What we take from this.** Quick Create already asks for destination first.
What it does not do is let you return to the clipper without starting over, and
it has no language step at all, because we do not yet have real transcription to
give a language to. Both are open items in the parity matrix.

## 6. Vendor choices worth noting

The vendor bundle includes Cropper, `rc-slider`, `bootstrap-slider`,
`video-react`, `react-slick`, `react-toggle`, Bootstrap, and Immutable.js. The
state tree is built from Immutable `List`/`Map` values.

**What we take from this.** Almost nothing — this is a 2016-era React stack and
our 240 KB bundle is a feature. The one genuine signal is Immutable.js: it points
at a document model edited through structural sharing, which is what makes undo
and revision history cheap. When we build revision history, that is the
constraint to design for, not the library to adopt.

## 6a. The editor must not promise what the renderer cannot do

The reference converts its configuration into render instructions for every
element — text overlays carry `textHtml`, `fontSize`, `fontFamily`, `lineHeight`,
`textAlign`, and a `position` expressed against a `viewport`, so a layer placed
on the canvas is a layer that appears in the export.

**What we take from this, and what we already did.** Ours did not. The renderer
drew one hard-coded waveform in one hard-coded place plus the transcript
captions, and ignored the scene entirely: background colour, layer positions,
layer timing, and every text layer you added were silently dropped at export.
The editor was lying about its own output.

`app/services/scene.py` is now the single translation from what the editor
stores — percentages of the canvas, seconds relative to the clip — into pixels
and FFmpeg expressions. The renderer honours the background, the waveform
layer's position, size, colour, and visibility, and draws text layers with
`drawtext` gated by `enable='between(t,…)'` so a layer's in and out points mean
something.

Two lessons that cost a render each, worth writing down:

- `showwaves` has no bar mode. Its `n` option is samples *per column*, so the
  filter consumes `n × width` samples per frame; a large `n` makes one frame span
  more audio than the whole clip and the filter emits nothing at all — silently,
  with exit code 0. Bars come from drawing into a narrow buffer and scaling up
  with `flags=neighbor`.
- `drawtext` needs an explicit `fontfile` wherever fontconfig is absent. On
  Windows it does not report an error; libfreetype takes the process down with an
  access violation. The font is resolved up front and text layers are dropped if
  none is found, and the container image now installs one.

The waveform's visual branch is normalised with `dynaudnorm` before `showwaves`.
Conversational speech sits far below peak, so an untouched signal draws a thin
line barely off the centre axis. Only the visual branch is affected; the exported
audio never passes through it.

## 7. Where we intend to be different

- **No analytics.** Four tracking scripts load before the app does. We ship none.
- **No mandatory cloud.** Transcription and rendering run on local GPUs.
- **Exports, not embeds.** We produce files on disk; there is no embed service.
- **Attribution is a build artefact.** Every render that uses a licensed track
  writes `CREDITS.txt` next to the MP4. See [`AUDIO_LIBRARY.md`](AUDIO_LIBRARY.md).
