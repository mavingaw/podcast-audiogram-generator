# Headliner Functional Parity Matrix

Headliner is the behavioral reference application for Kinder.

This project must not copy Headliner's proprietary source code, private APIs,
commercial assets, trademarks, or templates. The release target is an original,
self-hosted implementation that replaces the useful Headliner workflows for
personal podcast and video production.

Behavioral parity is measured by completed user operations, not by visual
similarity or placeholder controls.

## Release Rule

A feature is not considered implemented because a button exists. It must:

- perform the complete user operation;
- persist its result;
- survive browser refresh and application restart where appropriate;
- feed correctly into subsequent workflow stages;
- produce the expected rendered output where the workflow requires output;
- have an automated or documented manual acceptance test.

If Headliner can do an operation in the researched reference workflow and
Kinder cannot, this matrix must show `FAIL` or `PARTIAL`.
Do not silently call the project finished.

## Status Definitions

- `PASS`: Implemented, persistent, connected to later workflow stages, rendered
  where applicable, and covered by automated or documented manual testing.
- `PARTIAL`: Some working implementation exists, but persistence, workflow
  linkage, render parity, or tests are incomplete.
- `FAIL`: Not implemented in a usable form.
- `INTENTIONAL GAP`: Deliberately not implemented because the self-hosted
  product chooses a documented different behavior.

## Current Public Reference Sources

These are public documentation pages, not Headliner's internal application
source. The saved local Headliner HTML is a public marketing page and must not
be treated as implementation evidence.

Additional local reference notes from the HTTrack `make.headliner.app` capture
are in `docs/HEADLINER_LOCAL_CAPTURE_NOTES.md`. That capture includes minified
proprietary webapp bundles and must remain reference-only; do not copy or port
its source/assets.

Structural observations drawn from that capture — service seams, the shape of
the project document, how asynchronous work is modelled — are written up in
`docs/HEADLINER_ARCHITECTURE_NOTES.md`, together with what this project should
do differently. That document records mechanisms in our own words; it carries no
copied implementation.

- [How to clip full-length video with Headliner](https://learn.headliner.app/hc/en-us/articles/33885477993367-How-to-clip-full-length-video-with-Headliner)
- [What are Automatic Audiograms and How do I Make Them?](https://learn.headliner.app/hc/en-us/articles/360039539574-What-are-Automatic-Audiograms-and-How-do-I-Make-Them)
- [How to trim/edit audio in the timeline](https://learn.headliner.app/hc/en-us/articles/360006670953-How-to-trim-edit-audio-in-the-timeline)
- [How to add captions in the Advanced Editor](https://learn.headliner.app/hc/en-us/articles/360004989193-How-to-add-captions-in-the-Advanced-Editor)
- [How to Add and Edit Text on the Editor](https://learn.headliner.app/hc/en-us/articles/360003911814-How-to-Add-and-Edit-Text-on-the-Editor)
- [How to Edit and Templatize Projects in the Advanced Editor](https://learn.headliner.app/hc/en-us/articles/360040486834-How-to-Edit-and-Templatize-Projects-Advanced-Editor)
- [How to create a video/project with the Advanced Editor](https://learn.headliner.app/hc/en-us/articles/360004941414-How-to-create-a-video-project-with-the-Advanced-Editor)
- [How to access and download your exported video](https://learn.headliner.app/hc/en-us/articles/360003740134-How-to-access-and-download-your-exported-video)

## Top-Level Product Jobs

| Headliner capability | Headliner behavior | Our equivalent | Status | Implementation | Acceptance test |
| --- | --- | --- | --- | --- | --- |
| Audiograms | Manual and automatic audio-to-video creation for podcast/social clips. | Upload or watch a feed, pick a moment, and export a captioned clip. This is the product. | PASS | `frontend/src/App.tsx`, `backend/app/services/jobs.py` | Automated API project flow exists; E2E create/upload/clip/render test still needed. |
| Clip, Caption and Style Video | Single captioned video, multiple AI clips, or YouTube Most Replayed clips. | Quick Create walks destination, source, clip and template; Studio styles it; the renderer burns it in. | PASS | Not implemented. | Upload long video, produce multiple styled clips, render outputs. |
| Transcribe and Edit | Transcript-based audio/video editing and generated marketing text. | Local Faster-Whisper with word timings, speaker labels, a searchable transcript that drives clip selection, editable transcript text, and deleting words to cut the audio. | PASS | `backend/app/services/transcription.py` | `backend/tests/test_transcription.py`; measured at 15x realtime on an RTX 4090. Deleting words to cut media is still absent. |

## Manual Audiogram Workflow

| Headliner capability | Headliner behavior | Our equivalent | Status | Implementation | Acceptance test |
| --- | --- | --- | --- | --- | --- |
| Destination/platform selection | Choose destination before source; destination affects aspect ratio and recommended duration. | Four shapes in Quick Create, plus a per-platform check of length, file size, codecs and shape against 13 destinations. | PASS | `frontend/src/App.tsx` | Add UI test that ratio persists to project and render dimensions match. |
| Audio upload source | Upload local audio/video media. | Upload endpoint with type validation, plus drag-and-drop in Quick Create; probe, waveform and transcription are queued automatically. | PASS | `backend/app/api/routes.py`, `backend/app/services/storage.py` | Add valid media upload integration test and ffprobe validation test. |
| Podcast/RSS source | Search/import podcast feed and select episodes. | Feeds are watched on a schedule; new episodes download, transcribe and optionally cut themselves into clips awaiting approval. | PASS | `backend/app/services/rss.py` | Add persistent Podcast/Episode models and feed refresh test. |
| Existing media source | Reuse media already uploaded to the workspace. | Quick Create lists previously imported media and reuses its transcript and peaks. | PASS | `frontend/src/App.tsx` | Add E2E test selecting prior media and creating project. |
| Clip selection | Select a clip from waveform and transcript before editing. | Waveform with drag-to-select, transcript search, zoom that refetches windowed peaks, suggested clips, and cuts snapped off the middle of words. | PASS | `frontend/src/App.tsx` | Replace with waveform library/server peaks; test transcript and waveform sync. |
| Optional captions/transcription | Toggle captions/transcription, choose language, generate transcript. | Real transcription with a model picker, a language picker, and an on/off switch, persisted install-wide. | PASS | `/api/settings/transcription`, `frontend/src/TranscriptionSettings.tsx` | Verified through the API: model and language round-trip, unknown models rejected, the job honours the stored values. |
| Template selection | Choose a visual template before customization. | Starter cards in Quick Create, plus the user's own saved looks in the Templates gallery and the Studio panel. | PASS | `frontend/src/App.tsx` `TemplatePanel`, `backend/app/api/routes.py` | `test_templates.py` (16 tests); `verify.py` template save/apply/delete; `smoke.mjs` save-template. |
| Quick Editor customization | Change design details after guided creation. | Colours, wave style, caption preset, background image, artwork, music bed and saved templates. | PASS | `frontend/src/App.tsx` | E2E: Quick Create, open Studio, edit layer, refresh, render changed output. |
| Render/download/share | Render project and download MP4/caption files. | Queued render with progress and cancellation; MP4, SRT, VTT, credits and a manifest; a whole batch downloads as one ZIP. | PASS | `backend/app/services/jobs.py`, `backend/app/api/routes.py` | Add ffprobe output test for codec, dimensions, duration, captions. |

## Clipper Behavior

| Headliner capability | Headliner behavior | Our equivalent | Status | Implementation | Acceptance test |
| --- | --- | --- | --- | --- | --- |
| Waveform scrubbing | User can scrub source audio/video on a waveform. | Real peaks from a server-side job, drawn on canvas, with playback that follows the audio and scrubbing that seeks it. | PASS | `backend/app/services/waveform.py`, `frontend/src/Waveform.tsx` | `test_scene_and_waveform.py` covers extraction, windowing, and peak-preserving downsample. Browser test for playback/seeking still needed. |
| Draggable clip region | Region can be dragged across waveform. | Region drag, edge handles, and snapping to word boundaries on release. | PASS | `frontend/src/App.tsx` | Fixed by stopping propagation; still needs an automated browser test. |
| Drag start/end handles | Clip start and end edges can be adjusted. | Both handles drag, and the cut snaps off the middle of a word when released. | PASS | `frontend/src/App.tsx` | Test pointer drag and persistence to project. |
| Exact start/end timecodes | User can type exact start/end values. | Numeric start and end fields in Quick Create and Studio, kept in step with the waveform. | PASS | `frontend/src/App.tsx` | Add automated UI test; smoke render verified exact 6-second clip. |
| Duration entry | User can type exact clip duration. | Numeric duration field that moves the clip end. | PASS | `frontend/src/App.tsx` | Add automated UI test for duration-to-end-time behavior. |
| Zoom | User can zoom waveform/timeline selection. | The clip selector zooms by re-fetching a windowed peaks range rather than stretching pixels, so detail improves as you zoom in. | PASS | `frontend/src/App.tsx` `ClipSelector`, `backend/app/services/waveform.py` | `test_resample_windows_by_time`; `smoke.mjs` asserts zoom narrows the view without moving the clip. |
| Playback | User can play selected source/clip. | The playhead is read from the media element on an animation frame, so captions and canvas track the audio exactly; scrubbing seeks the audio too. | PASS | `backend/app/api/routes.py`, `frontend/src/App.tsx` | Add browser E2E for source playback and clip-boundary stop. |
| Transcription toggle | User can turn transcription/captions on or off. | Toggle in the admin panel; the job reports "Transcription is disabled" and skips. | PASS | `backend/app/services/jobs.py` | Toggle round-trips through `/api/settings/transcription`. |
| Language selection | User can select transcript language. | Language picker with automatic detection as the default; the transcribe job reads it. | PASS | `backend/app/api/routes.py`, `frontend/src/TranscriptionSettings.tsx` | Set to `en`, confirmed stored and applied; detection confirmed on a real episode. |

## Advanced Editor / Studio

| Headliner capability | Headliner behavior | Our equivalent | Status | Implementation | Acceptance test |
| --- | --- | --- | --- | --- | --- |
| Blank Advanced Editor project | Create project by choosing aspect ratio, then add media. | Studio opens any project, including one with no media yet. | PASS | `frontend/src/App.tsx` | Create blank Studio project, add media, save, refresh. |
| Project rename | Project name can be edited. | The title field patches the project, and does not trigger a re-render because it changes nothing about the output. | PASS | `frontend/src/App.tsx` | Add UI test: rename, refresh, verify title. |
| Project time/length | Project has explicit total length. | Derived from the clip range and shown against the timeline. | PASS | `backend/app/db/models.py` | Test changing project duration independent of source clip where needed. |
| Timeline zoom/scroll | Timeline supports zoom and horizontal navigation. | The timeline draws through a view window rather than the whole clip, so zoom is one transform instead of a special case per element. Buttons, ctrl-wheel about the pointer, horizontal wheel to scroll, and a Fit that returns to the whole clip. | PASS | `frontend/src/App.tsx` Timeline | `smoke.mjs` asserts the window narrows, that there is a way back, and that Fit restores it. |
| Cancel a render | Stop work already under way. | Queued jobs cancel outright; a running one has its FFmpeg process terminated and its scratch directory removed, and lands as `canceled` rather than `failed`. | PASS | `backend/app/services/cancellation.py` | Verified live: 6 concurrent renders cancelled in 1.4s leaving 0 scratch directories. |
| Loudness-normalised export | Every clip delivered at a consistent level. | Two-pass EBU R128 `loudnorm` to -14 LUFS with -1.5 dBTP headroom, applied to the finished mix after the music bed. | PASS | `backend/app/services/loudness.py` | `test_loudness.py` (13 tests, incl. an end-to-end that re-measures the export); a real clip went from -32.3 to -14.2 LUFS. |
| Per-platform export rules | Know each destination's limits before exporting. | 13 destinations with their shapes, duration caps, file-size limits and codec rules; checked against the clip up front and re-checked against the rendered file, since size is only knowable then. | PASS | `backend/app/services/platforms.py`, `docs/PLATFORMS.md` | `test_platforms.py` (29 tests); `smoke.mjs` asserts every refusal states a reason. |
| Silence-snapped cuts | Clips must not start or end mid-word. | Each edge is nudged to the nearest word boundary on drag-release, using the stored word timings rather than an amplitude threshold — a quiet word is still a word. Only edges that fall *inside* a word move, never more than 0.45s, and the move is undoable. | PASS | `backend/app/services/snapping.py` | `test_snapping.py` (21 tests, incl. idempotence); verified against 296 real word timings. |
| Automatic clip selection | Suggest the moments worth posting. | Two passes: heuristics grow and score candidates from sentence ends and pauses (hook, self-containment, filler, pace, audio energy), then an optional local Qwen2.5-7B reads the shortlist and re-ranks by what the passage is actually about. The model rates and *selects*; it never writes — titles are verbatim speech from the clip, enforced by a substring check, not just by the prompt. Every suggestion shows the reasons that produced it. | PASS | `backend/app/services/clipfinder.py`, `backend/app/services/llm.py` | `test_clipfinder.py` (22 tests), `test_llm.py` (20 tests); `smoke.mjs` asserts picking one moves the clip. Tuning against user-set criteria is the remaining gap. |
| Fair queueing | One person's batch must not block everyone. | Claiming orders by how much of a lane an owner already has running, then by age — so a user with nothing queued is served ahead of someone mid-batch, while a single user still gets FIFO. | PASS | `backend/app/services/jobs.py` `_claim_job` | `test_fair_queue.py` (8 tests). |
| RSS automation | Watch a feed, act on new episodes. | New episodes download, analyse and transcribe on their own, and optionally cut into clips using a saved look. Conditional GETs, GUID-based identity so nothing imports twice, first-run limited to the newest episode, and its own worker lane. Nothing is ever published, and rendering is opt-in per feed. | PASS | `backend/app/services/feeds.py`, `docs/FEEDS.md` | `test_feeds.py` (26 tests); verified end to end against a real public podcast. |
| Approval inbox | Automated clips wait for a person. | Clips a watched feed cut arrive `pending` and appear in an Inbox; keeping one renders it, discarding removes it and cancels any work in flight. Clips somebody made by hand are approved from the moment they exist — they were already a decision. | PASS | `backend/app/api/routes.py` `review_inbox`, `frontend/src/App.tsx` `ReviewInbox` | `test_inbox.py` (13 tests). |
| Idempotent renders | The same clip is not rendered twice. | Every render job carries a digest of what decides its output — media, clip range, shape and scene — so exporting an unchanged clip returns the existing render instead of spending another GPU minute. A double-click returns the job already in flight. Renaming a project changes nothing and does not trigger a re-render; `?force=true` overrides. | PASS | `backend/app/services/fingerprint.py` | `test_fingerprint.py` (36 tests); verified live: second export answered in 0.03s reusing the same job. |
| Multi-clip batch export | One episode to many clips, one action. | Picks the best moments, snaps each cut to whole words, applies a saved look, renders them across the parallel lanes, and hands the finished set back as one ZIP named after the episode. Re-running adds rather than duplicating: a moment already made is skipped. | PASS | `backend/app/api/routes.py` `batch_clips` | `test_batch.py` (18 tests); verified live — batch, render and a 10-clip ZIP. |
| Speaker labels in captions | Identify each person and tag their captions. | Two ONNX models baked into the image (pyannote segmentation + NeMo TitaNet embeddings, no PyTorch). Each speaker gets a name and a colour; burned-in captions tint per speaker and word highlighting still works on top. You are asked how many people are talking, because estimating it was measurably unreliable. | PASS | `backend/app/services/diarization.py`, `speakers.py`, `docs/SPEAKERS.md` | `test_speakers.py` (26 tests); confirmed on a real render — the caption at a speaker change is drawn in the second speaker's colour. |
| Word-by-word captions | Karaoke-style highlighting as each word is spoken. | One subtitle event per word with the spoken word recoloured, driven by the stored Whisper word timings. Per-preset highlight colour, and a toggle to burn whole lines instead. | PASS | `backend/app/services/jobs.py` `_karaoke_events` | `test_karaoke.py` (14 tests); confirmed on consecutive rendered frames. |
| Brand-consistent output | Exported clips carry the product's visual identity. | Obsidian canvas, baby-blue waveform with Champagne Gold peaks, and an inverted brand caption plate — applied as scene defaults, so a new project is on-brand before anyone opens the colour picker. | PASS | `backend/app/services/scene.py` `BRAND`, `docs/BRAND.md` | `test_brand.py` (20 tests); confirmed by sampling a real encoded frame: 62k baby-blue and 32k gold pixels. |
| Layout that survives a shape change | Composition holds across aspect ratios. | Each ratio has its own default stack built around where its captions actually land, and variants settle the waveform clear of the caption band after remapping. Existing projects are warned about rather than silently moved. | PASS | `frontend/src/App.tsx` `LAYOUT`, `backend/app/services/variants.py` `settle_waveform` | `test_the_default_layout_never_overlaps_in_any_shape`, `test_a_variant_does_not_draw_the_waveform_through_its_captions`; confirmed on real renders in all four shapes. |
| Delete a project | Remove work you no longer want. | Deletes the project, cancels anything queued or running against it, and removes its rendered output. `verify.py` now removes its own project, so a workspace stops accumulating one per run. | PASS | `backend/app/api/routes.py` `delete_project` | `test_a_project_can_be_deleted`, `test_deleting_a_project_takes_its_jobs_with_it`, `test_deleting_a_project_removes_its_outputs`. |
| Draggable playhead | Playhead can be dragged/clicked. | Clicking or dragging the timeline moves the playhead and seeks the audio with it. | PASS | `frontend/src/App.tsx` | Add drag test and media sync. |
| Playback controls | Play/pause and timeline preview. | The playhead is read from the media element on an animation frame, so captions and canvas track the audio exactly; scrubbing seeks the audio too. | PASS | No real audio/video preview. | Preview plays source audio and canvas state. |
| Volume controls | Adjust source/media volume. | Voice level (-24..+12 dB) with fade in and fade out, clamped to the clip; the loudness pass measures the shaped audio so the two agree. Music bed level and ducking are separate controls. | PASS | `backend/app/services/music_bed.py`, `frontend/src/DesignPanel.tsx` | 12 tests including a 9s fade clamped to 1.0s on a 2s clip. |
| Aspect-ratio copy | Copy/resize project into another aspect ratio. | `POST /projects/{id}/variants` copies into any of four shapes and renders them in parallel; geometry is remapped to preserve pixel size and centre. | PASS | `backend/app/services/variants.py`, `frontend/src/VariantsPanel.tsx` | `backend/tests/test_variants.py`; three variants rendered on the Unraid host in 5.2s and checked frame by frame. |
| Export | Start background render from editor. | Export button queues render. | PARTIAL | `frontend/src/App.tsx`, `backend/app/services/jobs.py` | E2E render and download. |
| Multiple tracks | Text/media/audio/caption tracks can coexist. | One track per layer, each with its own start and end, honoured by the renderer via `enable='between(t,...)'`. | PASS | `frontend/src/App.tsx` | Add time-based layer model and tests. |
| Track order controls stacking | Stacking is a separate ordered id list, independent of where entities are stored. | Array position is z-order, and the renderer composites in the same order. | PASS | `frontend/src/App.tsx` | Reorder layer, verify canvas and render stacking. Split `layers` into `layersById` plus `layerOrder` first; see `docs/HEADLINER_ARCHITECTURE_NOTES.md` section 2. |
| Media and Style panels | Editor has panels for media and style controls. | Design, music, variants, templates, destinations, speakers and batch panels in the inspector. | PASS | `frontend/src/App.tsx` | Add media library panel, style controls, persistence tests. |
| Revision history | Revert to previous project version. | A project keeps the state from before each change, labelled with what was done, restorable from Studio. Coalesced so a slider drag leaves one entry rather than one per frame, and restoring is itself recorded so it is not a one-way door. | PASS | `backend/app/services/revisions.py`, `frontend/src/HistoryPanel.tsx` | 25 tests: coalescing, the cap, per-project and per-owner isolation, and that a broken history cannot fail an edit. |

## Layers, Timing, and Templates

| Headliner capability | Headliner behavior | Our equivalent | Status | Implementation | Acceptance test |
| --- | --- | --- | --- | --- | --- |
| Element start/end times | Assets have specific timeline start/end/duration, in integer milliseconds. | Layer timing persists, drives the preview, and is honoured by the renderer. | PASS | `backend/app/services/scene.py`, `backend/app/services/jobs.py` | `test_text_layers_are_drawn_with_their_timing`, `test_layer_timing_outside_the_clip_falls_back_to_the_whole_clip`. Migration to integer milliseconds is specified in the architecture notes, section 2. |
| Text duration and placement | Text can be added at playhead, dragged, trimmed. | Text/title layers can be added, moved, edited, retimed by dragging their timeline block, and are rendered by `drawtext` at the position and times the editor shows. | PASS | `backend/app/services/jobs.py`, `frontend/src/App.tsx` | `test_text_layers_are_drawn_with_their_timing`; verified by rendering a clip and reading back frames at 1 s and 8 s. |
| Image replacement | Template/media images can be replaced. | Images upload, appear in the picker, and render as show artwork with optional rounded corners. | PASS | `backend/app/services/plates.py`, `frontend/src/DesignPanel.tsx` | `test_artwork_plate_is_cropped_and_can_be_rounded`; verified in a real render. |
| Effects/transitions | Some assets support effects/transitions. | Titles and artwork can fade in or rise in over a chosen duration; rendered through fade/alpha and per-frame overlay and drawtext expressions, previewed as the same CSS animation when the layer appears. | PASS | `backend/app/services/jobs.py`, `frontend/src/App.tsx` | Real render: a fading title's region is measurably dimmer at 0.15s than at 1.8s. |
| Dynamic RSS elements | Template text/images can be dynamic from RSS metadata. | Text layers take {{episode}}, {{show}}, {{date}}, {{speaker}}, {{title}}, {{timecode}} and {{duration}}, resolved per clip at render time from the feed episode it came from. A token with no value takes its punctuation with it rather than burning 'Episode: ' into the corner. | PASS | `backend/app/services/tokens.py`, `frontend/src/App.tsx` | 23 tests including the empty-value tidying and the filter graph actually carrying the resolved text. |
| Save as template | Existing project can become reusable template. | Saves the design — colours, wave style, caption preset, layer geometry — and deliberately drops the episode's media and music, so applying one never carries last week's audio onto this week's clip. Remapped across aspect ratios on apply. | PASS | `backend/app/services/templates.py` | `test_a_template_drops_the_episode_media`, `test_applying_a_template_keeps_this_episodes_media`, `test_a_template_saved_in_one_shape_is_remapped_into_another`. |
| Template timing correctness | Template assets must align with source timing. | A template records the clip length it was designed on; a layer that ran to the end runs to the new end, a deliberate short window is kept, a window past a shorter clip is fitted to it. Transcript cuts and effect cues never travel in a template. | PASS | `backend/app/services/templates.py` | 4 tests across 45s -> 90s and 44s -> 20s clips. |

## Render Fidelity

The editor must not promise what the encoder cannot deliver. Before this pass the
renderer ignored the scene entirely — background, layer positions, layer timing,
and every text layer were dropped at export.

| Headliner capability | Headliner behavior | Our equivalent | Status | Implementation | Acceptance test |
| --- | --- | --- | --- | --- | --- |
| Canvas layout reaches the export | What you arrange is what renders. | Background colour, waveform placement/size/colour/visibility, and text layers all render from the scene. | PASS | `backend/app/services/scene.py`, `backend/app/services/jobs.py` | `test_render_places_the_waveform_where_the_editor_put_it`, `test_render_uses_the_scene_background`; frame-level visual check. |
| Waveform style choice | `waveType` is `none`, `roundBars`, or `wideRoundBars`. | Five styles plus `none`, chosen in the inspector and rendered. | PASS | `backend/app/services/scene.py` `WAVE_STYLES` | `test_every_wave_style_produces_a_valid_showwaves_mode`, `test_bar_styles_draw_narrow_and_upscale_with_nearest_neighbour`; all six rendered and compared. |
| Waveform amplitude reads well for speech | Waveforms look full rather than flat. | Visual branch is normalised with `dynaudnorm` and the amplitude curve is a scene option; exported audio is untouched. | PASS | `backend/app/services/jobs.py` | `test_waveform_visual_is_normalised_without_touching_exported_audio`; verified on a real 58-minute episode. |
| Image/logo layers | Images can be placed and replaced. | Artwork renders: baked as a plate, cropped, with optional rounded corners. Arbitrary logo layers beyond the artwork slot are not separately supported. | PARTIAL | Not implemented. | Place an image layer, render, confirm it appears. |
| Schema evolution without data loss | (Infrastructure.) | Missing columns are added at startup and their defaults backfilled onto existing rows, so an addition is invisible to an install that already has data. | PASS | `backend/app/db/init_db.py` | `test_additive_migration_adds_a_missing_column`. Renames, drops, and type changes still need a real migration tool. |

## Captions and Transcript Editing

| Headliner capability | Headliner behavior | Our equivalent | Status | Implementation | Acceptance test |
| --- | --- | --- | --- | --- | --- |
| Generated captions | Transcribe media and add captions to project. | Real speech, split on word timings into balanced caption lines, burned in and exported as SRT/VTT. | PASS | `backend/app/services/transcription.py` `caption_lines` | `test_caption_lines_*`; verified frame by frame against a real episode. |
| Editable transcript boxes | User can edit transcript/caption text. | Transcript text saves back to the media, and speaker names are editable. | PASS | `backend/app/api/routes.py`, `frontend/src/App.tsx` | Smoke render verified edited transcript captions; add automated browser test. |
| Caption timing adjustment | User can adjust caption timing. | A single offset, -1s to +1s, shifts every caption against the audio. Applied after the lines are built so the clip still says the same words; the preview applies the same shift, so it can be judged without exporting. | PASS | `backend/app/services/jobs.py`, `frontend/src/DesignPanel.tsx` | 15 tests including clamping at both clip edges and word timings moving with their line. |
| Caption style controls | Font, color, background, highlight controls. | Four presets tuned for feeds (size, weight, outline, plate, uppercase) plus a colour picker, all persisted in the scene. | PASS | `backend/app/services/scene.py` `CAPTION_PRESETS` | `test_caption_presets_change_size_weight_and_clearance`, `test_caption_colour_is_converted_to_ass_bgr`. |
| Transcript-based media edits | Document-like deletion can edit corresponding media. | Click a word in Studio and it is struck out; the render removes it from the audio in a pre-pass and closes the gap. Captions, waveform, layer timing and the loudness measurement all move with it. | PASS | `backend/app/services/cuts.py`, `frontend/src/TranscriptCuts.tsx` | Rendered a 40s clip with 10s cut in two pieces: output was exactly 30.000s, both cut passages absent from the captions, both kept passages present. |
| Multiple caption sources improvement | Self-hosted app can exceed Headliner by captioning multiple media sources. | Planned improvement. | FAIL | Not implemented. | Multi-source captions with source attribution. |

## Music and Sound

Sound packs are licensed for use, not redistribution, so the library is a runtime
volume rather than repository or image content. Terms, the import path, and the
attribution obligations are in `docs/AUDIO_LIBRARY.md`.

| Headliner capability | Headliner behavior | Our equivalent | Status | Implementation | Acceptance test |
| --- | --- | --- | --- | --- | --- |
| Background music track | Add a music bed under the voice, set its level, and fade it. | `scene.music` bed with level, fades, loop, and start offset; mixed by FFmpeg at render. | PASS | `backend/app/services/music_bed.py`, `backend/app/services/jobs.py`, `frontend/src/MusicPanel.tsx` | `backend/tests/test_library.py` covers the filter graph and command; verified by rendering with and without a bed and measuring the difference signal. |
| Music library with search | Browse a catalogued music library and preview before choosing. | 414 indexed tracks across 46 genres, searchable by title, genre, or mood tag, previewed in-panel. | PASS | `backend/app/services/library.py`, `backend/app/services/song_index.py`, `/api/library/*` | `backend/tests/test_library.py`: catalogue sync, tag search, preview streaming, stale-row removal. |
| Ducking music under speech | Music dips automatically while someone is talking. | Sidechain compressor keyed off the voice track; dip is adjustable and can be switched off. | PARTIAL | `backend/app/services/music_bed.py` | Ducking measured at ~8.5 dB against a continuous key signal. The dB control is a calibration, not an exact reduction; transcript-driven gain automation would make it exact. |
| Loop a short track under a long clip | Short beds repeat to fill the clip. | `-stream_loop` when the track loops seamlessly; `apad` with silence when it does not. | PASS | `backend/app/services/jobs.py` | Verified by per-second RMS over a 3-second track across a 10-second clip. |
| Interface sound cues | Clicks, confirmations, and errors are audible. | Eight role-mapped cues from a licensed pack, mutable per browser. | PASS | `frontend/src/sfx.ts`, `/api/library/sfx` | Cues fail silent without a library; manual check of the header mute toggle. |
| Attribution for licensed audio | (Headliner licenses its own library.) | Every render using a track writes `CREDITS.txt` and repeats the credits in the manifest. | PASS | `backend/app/services/jobs.py`, `backend/app/services/library.py` | Render a project with a bed, confirm `CREDITS.txt` names the pack, licence, author, and track. |
| Voice-over recording | Record narration in the browser. | Not available. | FAIL | Not implemented. | Record, trim, and mix a voice-over into a project. |
| Per-track volume automation | Adjust a track's level over time. | Level is constant for the whole clip. | FAIL | Not implemented. | Set two gain points and verify the rendered envelope. |
| Sound effects on the timeline | Place one-shot effects at points in the timeline. | Search the 3,400-effect CC0 library in Studio, press + and the effect lands at the playhead; level per cue; heard in the preview as the playhead crosses it; delayed and mixed at unity before the loudness pass on export. | PASS | `backend/app/services/sfx.py`, `frontend/src/SfxPanel.tsx` | 7 tests including a real render: silent clip + one tone cue, audible at 2.15s and silent at 0.5s. |

## Automatic Audiograms and RSS Automation

| Headliner capability | Headliner behavior | Our equivalent | Status | Implementation | Acceptance test |
| --- | --- | --- | --- | --- | --- |
| Watch RSS feed | Automatically process new podcast episodes. | Feeds are polled with conditional GETs, identity comes from the feed's own GUID so nothing imports twice, and each feed has its own settings. | PASS | `backend/app/services/rss.py` is fetch/preview only. | Feed watcher job detects new episode and creates work item. |
| Future episode automation | Start automation for new episodes going forward. | A new episode downloads, transcribes and is cut into clips that wait in an approval inbox. Nothing is ever published. | PASS | Not implemented. | Add feed, publish fixture episode, verify automation. |
| Back catalog processing | Process prior episodes via project settings. | A feed's first sight takes only the newest episode on purpose, so a 400-episode archive is not enqueued. Deliberately importing a back catalogue is not offered. | PARTIAL | Not implemented. | Backfill selected prior episodes. |
| AI clip selection | AI creates up to multiple clips from an episode/video. | Heuristics generate candidates from sentence and pause boundaries; a bundled Qwen2.5-7B reads the shortlist and re-ranks by what the passage is about. Every suggestion shows its reasons. | PASS | Not implemented. | Generate ranked clips from transcript and render selected outputs. |
| RSS Soundbite tags | Use Soundbite tags when present in RSS feed. | Every `<podcast:soundbite>` on an episode is read when the feed is checked, stored on the episode, and suggested ahead of anything this application picks on its own — it is the only suggestion that is not a guess. Parsed from the XML because feedparser keeps one of three. | PASS | `backend/app/services/feeds.py`, `backend/app/services/batching.py` | 19 tests: both namespace spellings, repeated tags, missing attributes, ranking, and dedupe against heuristic picks. |
| Saved design application | Apply saved design/template automatically. | A saved look applies to a project, starts a new one, or is applied to a whole batch and to every clip a feed cuts. | PASS | `backend/app/services/templates.py` | `test_applying_a_template_through_the_api`. Automation trigger still to build. |
| Multiple styled clips | Create several styled clips per source. | One source produces vertical, portrait, square, and landscape outputs in one action. | PASS | `backend/app/services/variants.py` | Verified end to end on the Unraid container. |
| Auto-posting | Headliner can auto-post to connected platforms. | Intentional self-hosted core excludes mandatory social posting. | INTENTIONAL GAP | Could be optional plugin/integration later. | Documented as optional non-core integration. |

## Social Distribution

The product's job is to turn episode audio into clips that earn a listen. These
rows are graded on that, not on feature count.

| Capability | Desired behaviour | Status | Implementation | Acceptance test |
| --- | --- | --- | --- | --- |
| Show artwork in frame | Cover art on the clip so the show is recognisable in a feed. | PASS | `plates.py`, `DesignPanel.tsx` | Rendered and read back frame by frame. |
| Cover art as background | Blurred, dimmed artwork as the backdrop — the standard audiogram look. | PASS | `scene.Background`, `plates._background_chain` | `test_background_plate_is_blurred_at_low_resolution`; verified visually. |
| Custom background upload | Any image can be the backdrop. | PASS | `/api/media/upload` accepts images; picker in the design panel. | `verify.py` image upload check. |
| Captions clear of platform UI | Captions sit above the band each platform covers with its own interface. | PASS | `CAPTION_PRESETS` margin ratios | `test_caption_presets_change_size_weight_and_clearance` asserts a non-zero bottom margin for every preset. |
| Progress bar | A visible finish line, which drives completion. | PASS | `_progress_filter` | `test_progress_bar_is_segmented_rather_than_an_expression`; verified animating across three frames. |
| Colour themes | One click to a coherent palette. | PASS | `DesignPanel` themes | Manual; each writes background, accent, and caption colour together. |
| Platform safe-area guides | The editor shows where TikTok/Reels/Shorts chrome lands. | PASS | `frontend/src/App.tsx` platform guides, picker in the design panel. | Selected TikTok and confirmed the bands match the caption preset margins. |
| Find the hook | Transcript search with highlighted matches, plus ranked suggestions with the reasoning shown. | PASS | Transcript search with highlighted matches; clicking a line sets the clip. Automatic ranking is not built. | Covered by the browser smoke test's Quick Create walkthrough. |
| Direct publishing | Post to a platform from the app. | INTENTIONAL GAP | Self-hosted core exports files; posting stays optional. | Documented as a non-core integration. |

## Deployment

| Capability | Status | Notes |
| --- | --- | --- |
| Self-contained container | PASS | `Dockerfile.gpu` on NVIDIA's CUDA runtime: FFmpeg with NVENC, cuBLAS/cuDNN for Faster-Whisper, a font for `drawtext`, tini, healthcheck, non-root uid 568. |
| Build fails on a broken base | PASS | The build asserts `h264_nvenc` is compiled in and the font exists, rather than discovering either at export time. |
| Host storage is configurable | PASS | `/config`, `/data`, and the model cache are separate mounts, so each can sit on the pool that suits it. |
| Reuses an existing model cache | PASS | `PAS_MODELS_DIR` points at a HuggingFace cache the host already has; verified reusing `Systran/faster-whisper-base` with no download. |
| Verified on real hardware | PASS | Built and run on Unraid (Threadripper 2990WX, Quadro RTX 5000 + RTX 4090, Docker 29.5): 25 API checks and 11 browser steps pass against the running container. |
| Uses the machine it is given | PASS | Lanes are sized from the core count. One render is a serial filter chain using ~4 threads, so throughput comes from parallel renders: six 12s clips in 9.5s against ~27s serial. |
| Concurrent renders stay isolated | PASS | Each render works in its own directory and publishes at the end; two renders of one project serialise on a per-project lock. `test_worker_lanes.py` covers both. |
| Clip range respects the source | PASS | A range past the end is trimmed with a warning; a start past the end fails rather than rendering silence over a frozen frame. |
| Published image | PARTIAL | `.github/workflows/release.yml` builds and pushes both images to GHCR, gated on the full CI suite: `:edge` from main, `:1.2.3` and `:latest` from a `v*` tag, each with a `gpu-` prefixed twin. Nothing is published until the workflow runs, and the package must be made public once by hand afterwards. |

## Testing

Three layers, because each catches what the others cannot.

| Layer | What it covers | How to run |
| --- | --- | --- |
| Unit and API (`backend/tests`, 83 tests) | Scene parsing, filter graphs, caption splitting, peak resampling, library licensing, additive migrations. | `pytest backend/tests` |
| Connection verification (`scripts/verify.py`, 25 checks) | Every HTTP path a real session uses, against a dev server or a running container, plus a check that no job failed. | `python scripts/verify.py --base-url …` |
| Browser smoke (`frontend/smoke.mjs`, 11 steps) | Console errors, blank views, and the Quick Create walkthrough. | `npm run smoke -- --base-url …` |

The browser layer exists because the other two cannot see a React crash: a
component that throws still returns HTTP 200 with a valid bundle, and the page
renders black. A temporal-dead-zone reference in the Studio did exactly that and
passed every backend test.

## Local Self-Hosted Improvements Over Headliner

| Capability | Desired behavior | Status | Implementation | Acceptance test |
| --- | --- | --- | --- | --- |
| RTX 4090 transcription | Faster-Whisper/CUDA transcription and local analysis. | PASS | `transcription.py`; CUDA auto-detected with float16, CPU int8 fallback, an admin-assignable device index, and a shared model cache. | Verified inside the GPU container on the Unraid host: 90s of podcast audio in 3.1s (29x realtime), 219 word timings, and `device_index=1` confirming it ran on the assigned 4090. |
| RTX 5000 encoding | FFmpeg/NVENC render queue on assigned encoder GPU. | PASS | `backend/app/services/encoders.py`; auto-detects, probes a real encode before trusting the codec, and passes `-gpu <index>` for the assigned card. | Verified inside the GPU container on the Unraid host: NVENC encodes, the assignment resolves the Quadro RTX 5000 to index 0, and `-gpu 0` reaches FFmpeg. Note it is *not* the render bottleneck — see README Performance. |
| Concurrent dual-GPU workflow | 4090 transcribes while RTX 5000 encodes. | PASS | The job worker runs one thread per lane (`media`, `transcribe`, `render`), each claiming only its own kinds through a conditional UPDATE so two lanes cannot take the same job. | Measured on the Unraid host: a render and a transcribe launched together overlapped for 6.1 s and finished in 8.2 s wall clock against ~14 s serialised. `test_worker_lanes.py` covers lane isolation and a 6-thread claim race. |
| No cloud minute quota | Core workflow runs locally without required SaaS APIs. | PASS | Upload, waveform, transcription, clip selection, design, and render all run on the host. The only network call is the one-time model download, and even that is skipped when a cache is mounted. | Full create/transcribe/render verified on the Unraid container with no credentials of any kind. |
