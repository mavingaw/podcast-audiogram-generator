# Kinder — operations log & disaster recovery

Last updated: 2026-08-30. This is the "if something breaks, start here" file.
It records what was built, what went wrong along the way (so nobody re-hits
it), and exactly how to bring everything back.

## What Kinder is

Self-hosted podcast clip/audiogram studio (a Headliner replacement, every Pro
feature, free). Runs on the Unraid box **192.168.1.58** as Docker container
**`Kinder`**, LAN http://192.168.1.58:8099, public **https://kinder.skdcorp.com**
through the `cloudflared-skdcorp` tunnel. Source of truth for the code:
`C:\Users\mavin\Downloads\podcast-audiogram-studio` (git repo, fork of
github.com/mavingaw/podcast-audiogram-generator).

## The container stack (all on 192.168.1.58)

| Container | What it does | Where |
|---|---|---|
| `Kinder` | the app (FastAPI + React + FFmpeg + Whisper + local LLM, 2 GPUs) | :8099, public via tunnel |
| `kinder-db` | Postgres 16 — the app's database | :5433, data in `/mnt/storage/appdata/kinder-db` |
| `kinder-db-backup` | automatic **nightly database dumps**, keeps 14 daily + 8 weekly | dumps in `/mnt/storage/appdata/kinder-db-backups` |
| `dozzle` | live logs of every container in the browser | http://192.168.1.58:8092 |
| `uptime-kuma` | uptime monitor + alerts (was already on the box) | http://192.168.1.58:3011 |
| `kinder-file-backup` | daily copy of database file, uploads & finished videos | `/mnt/storage/backups/kinder-files` |
| `autoheal` | restarts any container whose healthcheck turns unhealthy | — |
| `kinder-adminer` | web UI to inspect the database | http://192.168.1.58:8094 (server `kinder-db`, user `kinder`) |
| `cloudflared-skdcorp` | the tunnel that makes it public | — |

`kinder-db`, `kinder-db-backup`, `kinder-adminer` share the Docker network
`kinder-net`. The stack is recorded in `docker-compose.helpers.yml` at the
repo root so it can be rebuilt anywhere; on the box the containers were
created with plain `docker run` (Unraid is template-managed, not compose-run).

## Where the data lives (back these up and nothing is ever lost)

- `/mnt/storage/appdata/kinder-db` — Postgres data (the database itself)
- `/mnt/storage/appdata/kinder-db-backups` — nightly SQL dumps of the above
- `/mnt/storage/appdata/podcast-audiogram-studio/config/app.db` — the old
  SQLite database, kept frozen as an instant-rollback copy
- `/mnt/storage/appdata/podcast-audiogram-studio/data` — uploads, rendered
  videos, music/sfx library
- `/boot/config/plugins/dockerMan/templates-user/my-Kinder.xml` — the
  container template (ports, env vars incl. secrets)
- The git repo on the Windows PC (full history of every change), plus full
  history bundles at `Documents\kinder-backup\` and
  `/mnt/storage/backups/kinder-files/kinder-repo-*.bundle` on the box
  (restore with `git clone kinder-repo-<date>.bundle`)

## If something breaks and nobody technical is around

The current fully-verified build is frozen as a named safe point:
git tag `v1.0-stable` (on GitHub too) and Docker image
`podcast-audiogram-studio:stable` on the box. To return the app to it:

    ssh root@192.168.1.58
    docker tag podcast-audiogram-studio:stable podcast-audiogram-studio:gpu
    /mnt/storage/appdata/podcast-audiogram-studio/apply-template.php Kinder

That is the whole rollback - about fifteen seconds, no rebuild, no code.
Retag a new `:stable` only after a build has passed every check and soaked.

## Deploy recipe (the only correct way to ship a change)

1. `scp` changed files to
   `root@192.168.1.58:/mnt/storage/appdata/podcast-audiogram-studio/build/src/<same path>`
2. `cd .../build/src && docker compose -f docker-compose.gpu.yml build podcast-audiogram-studio`
3. `/mnt/storage/appdata/podcast-audiogram-studio/apply-template.php Kinder`
   — **never `docker restart`**: restart keeps the OLD image silently.
4. Wait ~15 s, `curl http://192.168.1.58:8099/api/health`, then run
   `node frontend/smoke.mjs` and `node frontend/regression.mjs` against the
   public URL.

Env vars only reach the app if they are in the **template XML** (compose's
`.env` does not).

## Database: Postgres move (2026-08-30)

- The app reads `DATABASE_URL`; unset = SQLite at `/config/app.db`,
  set = Postgres. The box's template sets
  `DATABASE_URL=postgresql+psycopg2://kinder:...@192.168.1.58:5433/kinder`
  (password in the template XML on the box, not in git).
- Data was copied with `scripts/migrate_to_postgres.py` (row-count verified).
- **Rollback**: remove `DATABASE_URL` from the template, apply-template —
  the app is back on the frozen SQLite file instantly.
- **Restore a nightly dump**:
  `zcat /mnt/storage/appdata/kinder-db-backups/daily/<file>.sql.gz | docker exec -i kinder-db psql -U kinder kinder`
- Why no Redis: the job queue is deliberately database-backed so renders
  survive restarts; nothing in the app needs a cache/pub-sub layer.
- Postgres is tuned for the box (188 GB RAM): `shared_buffers=2GB`,
  `effective_cache_size=12GB`, `work_mem=32MB`, `wal_compression=on` — set
  via `ALTER SYSTEM` (one statement per `psql -c`, it refuses batches).
  Indexes added on `projects/jobs/media_assets (owner_id)` and
  `jobs (subject_id)`. The app's engine uses `pool_pre_ping`, so a database
  restart costs one silent reconnect, not a burst of errors.

## If the whole box died tomorrow (full rebuild)

1. New Unraid/Docker host, restore `/mnt/storage/appdata` from array/backup.
2. Clone the repo, `docker compose -f docker-compose.gpu.yml build`.
3. Re-create the Kinder container from `my-Kinder.xml` (or apply-template.php).
4. `docker compose -f docker-compose.helpers.yml up -d` for the helper stack.
5. Restore the newest dump from `kinder-db-backups` into `kinder-db`.
6. Point the Cloudflare tunnel at :8099 again.

## Biggest gotchas we hit (do not re-learn these the hard way)

- **`docker restart` keeps the old image.** Always apply-template.php.
- **Cloudflare cached `/api/...mp4` for 4 h** — re-exports looked broken.
  Fixed in code (no-store on /api + `?v=` on downloads); verify over LAN.
- **Damaged MP3s silently truncate transcripts** (decoder stops at the bad
  frame). Fixed: audio is decoded to WAV via ffmpeg before Whisper.
- **`npx tsc | tail` swallows the exit code** — always check `tsc exit: $?`.
- **Test client reloads a fixed module list** — every new backend service
  must be added in `tests/test_api.py` or ~17 unrelated tests fail.
- **Custom fonts**: register before `parse_scene`, not after.
- **Intro/outro concat with `-c copy` doubled audio duration** — audio is
  re-encoded and durations verified.
- **Unraid box is invisible to ping sweeps** — it's at 192.168.1.58 (BMC .104).
- **uptime-kuma already existed on the box** (:3011) — don't start a second.
- **The shared Whisper model cache** (`/mnt/storage/appdata/whisper/hub`)
  grows `.locks` entries owned by other services — a friend's transcription
  died on `Permission denied`. Fixed with `chown -R 568` + a default ACL so
  new lock files inherit permission. If it recurs: same chown, then
  "Transcribe again" on the episode.
- **Corrupt MP3 uploads** ("Header missing") fail the waveform job; the app
  survives, the episode shows a failed state with a retry button.

## Feature log (all verified with real renders, condensed)

Everything Headliner Pro sells, working and free: transcription (GPU
faster-whisper), AI clip suggestions with sponsor-read detection, AI show
notes, captions (karaoke/pill/plates), all sound-bar styles with matching
previews, watermarks from the show's own artwork, intro/outro clips, custom
fonts, video-footage backgrounds, 12 starter templates + 16 palettes, share
pages with posters + analytics (views/plays), trash with 7-day keep, profile
system (avatar/display name/password), right-click menus everywhere,
resizable canvas layers, guided coach + plain-language help, upload/
transcribe progress with fun facts, Simple/Everything studio modes,
social-posting framework (YouTube complete; Meta/TikTok/LinkedIn/Pinterest/X
wired, waiting on API keys), invite-code signups. Benchmark: 66-min episode →
uploaded, transcribed, 10×2-min clips in **5m46s**.

2026-08-30 late: five fixes from friend feedback, all verified with real renders
and the live smoke: cover-art mouse drag (native image drag hijacked the pointer),
instant arrow nudges, draggable captions (scene.captionY), live bars mirrored into
a centred EQ mountain (was a lopsided spectrum wedge), cover-art upload chip, and
eight new templates with genuinely different layouts.

2026-08-31: five-agent full review (backend API, render services, frontend,
infrastructure, live QA). 17 findings fixed in commit 4621cb9 — highlights:
signup-code throttle now enforced; batch-export ZIP was corrupt with 2+ clips
(rebuilt over a temp file); password change revokes other sessions; GPU
settings admin-only; LLM clip scorer now honours the generation lock (crash
risk); loudness pass got a timeout; branding intro/outro keyed by role; font
family names sanitized before ASS; plus seven frontend state/debounce/touch
fixes. OPEN ITEM FOR THE OWNER: the Unraid SMB [storage] share is guest ok +
writable — the whole LAN can read/write the Postgres dir, dumps, and backups;
fix in Unraid Shares -> storage -> Security (private/secure), or exclude
appdata + backups from the export. Dozzle (:8092) is unauthenticated log
access to every container. Deferred findings live in the session triage notes
and the repo issues-of-record below.

2026-08-31: GPU roles swapped to the sensible split (owner's architecture note
was right about the routing): Whisper transcription -> Quadro RTX 5000 (GPU-39ad,
16GB is ample for distil-large-v3), local LLM + NVENC encodes -> RTX 4090
(GPU-8b1b; PAS_LLM_GPU=1 in the template). All via the app's own runtime
settings - no container split needed. The rest of that note's stack (vLLM,
separate Whisper/FFmpeg containers, aigate, Redis, MinIO) is mapped in
docs/BLUEPRINT_TRIAGE.md: in-process on one box beats IPC between six
containers, the Whisper model is already held resident (_model_cache), and
MinIO is an S3 API on the same disk the app reads natively.

Nightly: Windows scheduled task "Kinder nightly check" (03:30) runs the
public smoke + regression and logs to `runtime/logs/`.

2026-09-02: Studio panel reshuffle + two export bugs. (1) The export now
composites layers in the order the panel lists them ("Bring forward" used to
change only the preview; every picture went under the sound bars and every
title over the captions regardless) — tests/test_layer_order.py; verified
with two real renders on the box (default order and artwork-over-bars). (2) A
retyped transcript word now reaches the burned-in captions (they were built
from Whisper's words, so corrections never showed) — "Fix a word" in Cut the
clip; tests/test_edited_captions.py. (3) An expired/bad share link shows a
friendly page instead of raw JSON. Panel: Pictures first (cover art + upload),
one Text section (words of the selected text layer + caption style/font/size),
Colours, then Everything-only dials; layer arrows are Bring forward / Send
back and disable at the ends of the stack. Smoke: 32 steps (new fix-word,
layer-order). DEPLOY GOTCHA: `docker compose build` on the box needs
KINDER_DB_PASSWORD exported (the compose file declares kinder-db) — without it
the build fails before it starts and apply-template quietly reuses the old
image. Read it from the template XML on the box; never write it to .env.

2026-09-02 later: Safari drag fix (Afiya's "cropping the cover art with the
cursor does not work"). Reproduced in Playwright's WebKit: a 40 px corner
drag resized by 4 px and a 30 px move moved 3 px, while Chromium did the
full amount. Cause: drag() and resize() updated the layer inside a React
functional state updater, which React only runs when it next renders;
Safari delivers pointer moves faster than that, so at pointerup the ref
still held the FIRST move's position, save() sent it, and the response
snapped the picture back. Both handlers now compute from the ref
synchronously. Also: transcript saves are queued one at a time and only the
newest response is applied (two quick word fixes could put the old word
back). Smoke has --engine webkit; the nightly runs it as a fourth pass.
Deployed as image 1e009cdb0717; verified with the WebKit probe against the
box, Chromium smoke 32/32, WebKit smoke 32/32 (local), phone smoke.
Afiya's other reports: her dropdown screenshot shows sound-bar names that
left the app on Aug 30, so her browser was holding an old copy of the app;
the public URL serves the current bundle. A hard refresh (Cmd+Shift+R) or
a fresh tab gets her the Aug 30 sound-bar fix and today's panel changes.

2026-09-02 evening: failed saves are no longer silent (deferred item from the
Aug 31 review). A project or transcript save that errors now shows a notice
at the bottom of the screen and reloads what the server actually has, so
the edit does not sit on screen unsaved until a reload quietly removes it.
Smoke step "save-failure" fails one PATCH with a 500 and checks the notice
appears, the layer snaps back, and OK dismisses it. Image 456caf00df07.
This morning's nightly (03:30) FAILED on the keyboard step: the captions band
was swallowing the click on the title, which the caption-handoff change in
the first deploy today removed; every smoke since passes it.
Reply to Afiya drafted in Gmail (not sent) — it tells her to hard-refresh.
