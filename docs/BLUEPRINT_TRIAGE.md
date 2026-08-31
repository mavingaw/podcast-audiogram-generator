# Blueprint triage — the "next-generation platform" document vs. Kinder

The owner supplied an architectural blueprint for an ideal Headliner
successor. This is the honest mapping: what Kinder already does, what we are
adopting, and what contradicts the reasons Kinder exists (self-hosted, free,
no external keys, one box).

## Already true in Kinder

- Text-based editing: click a word to cut it; the export follows exactly.
- Direct manipulation: drag layers, drag captions, resize on the canvas.
- Progressive disclosure: Simple/Everything, Design folds, layer inspector.
- Non-destructive history: revisions with Restore, trash with 7-day keep.
- No duration caps, no ads, no watermark-except-your-own, everything free.
- AI curation: clip suggestions with sponsor-read detection, show notes with
  titles/hashtags, speaker diarization with per-speaker caption tint.
- Karaoke/kinetic captions incl. pill and plate styles; 7 sound-bar styles.
- Platform guides ("Where is this going?" shades each app's own chrome).
- Chunked multi-hour uploads with gateway-hiccup retries; phone support.
- Dark-first, Material 3, Inter — the blueprint's exact aesthetic notes.

## Adopted from the blueprint (this pass)

- **Omnibar aspect switch**: 9:16 / 4:5 / 1:1 / 16:9 toggle in the Studio
  toolbar; the template remap machinery carries the whole design across and
  the platform guide follows the shape. `POST /projects/{id}/aspect/{ratio}`.
- **One-click transcript download**: SRT / VTT / TXT links right on the
  Full-transcript pane. `GET /media/{id}/transcript.{fmt}`.

## Worth adopting later (queued)

- One-click "magic clip" from an episode card (wire batch count=1 + render).
- "Clean up the sound" toggle (ffmpeg denoise/de-ess chain; loudnorm exists).
- Ken Burns drift on background images (ffmpeg zoompan, subtle, optional).
- Silence-a-word (keep timing, mute audio) beside cut-a-word.
- Semantic emoji suggestions in captions via the local LLM (opt-in).
- 4K export tier (NVENC can; cost is render time on the 2 GPUs).

## Rejected — contradicts what Kinder is

- Deepgram/AssemblyAI/ElevenLabs/Twelve Labs/GPT-4o: metered external APIs
  with keys and bills. Kinder's whole premise is local Whisper + local LLM,
  $0 forever, nothing leaves the box.
- Kubernetes microservices, H100 inference nodes: it is one Unraid box with
  two consumer GPUs, and that is the point.
- Wasm client-side rendering: the box's GPUs render faster than any
  viewer's laptop; server rendering here IS the low-latency path.
- Stock B-roll libraries (Storyblocks): licensing + external dependency;
  the local FMA/Freesound libraries and the user's own uploads fill this.
- Pricing tiers, credits, watermarked free tier: everything is free here.
