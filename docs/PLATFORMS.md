# Platform requirements

Every destination has its own ceiling on length and file size, its own list of
shapes, and its own codec rules. An export that misses one fails at the upload
step — after the render, after the wait, usually on a phone. `Where this can go`
in the Studio sidebar answers that before the GPU time is spent.

The table lives in `backend/app/services/platforms.py`.

## What is checked

| Check | Blocking? | Why |
|---|---|---|
| Duration under the platform's cap | yes | The most common refusal by far |
| Duration over its minimum | yes | Several platforms reject very short clips |
| File size under the cap | yes | Only knowable after a render, so it is re-checked then |
| Aspect ratio supported | yes | An unsupported shape is cropped or refused |
| Aspect ratio *preferred* | no — warning | It will upload; it will be a worse post |
| Container, video codec, audio codec | yes | Kinder always writes MP4/H.264/AAC, so these pass everywhere today |

Blocking problems will fail the upload. Warnings will succeed and produce
something worse — the wrong shape on a vertical feed means bars where the
picture should be.

## The table

Verified against each platform's published creator documentation in **May
2026**. Every entry carries a `checked` date and a `notes` line.

**These numbers change without notice**, and several differ between the mobile
app and the web uploader. Where the two disagree the *lower* limit is used: a
spec that says yes and an upload that says no is worse than one that is slightly
cautious.

| Platform | Shapes | Preferred | Max length | Max size |
|---|---|---|---|---|
| TikTok | 9:16, 1:1, 16:9 | 9:16 | 10 min | 500 MB |
| Instagram Reels | 9:16 | 9:16 | 3 min | 4 GB |
| Instagram Feed | 4:5, 1:1, 16:9 | 4:5 | 60 min | 4 GB |
| Instagram / Facebook Stories | 9:16 | 9:16 | 60 s | 4 GB |
| YouTube Shorts | 9:16 | 9:16 | 3 min | 2 GB |
| YouTube | 16:9, 9:16, 1:1, 4:5 | 16:9 | 12 h | 256 GB |
| Facebook Reels | 9:16 | 9:16 | 90 s | 4 GB |
| Facebook Feed | 16:9, 1:1, 4:5, 9:16 | 1:1 | 240 min | 10 GB |
| LinkedIn | 16:9, 1:1, 4:5, 9:16 | 1:1 | 10 min | 5 GB |
| X / Twitter | 16:9, 1:1, 9:16 | 16:9 | 2 min 20 s | 512 MB |
| Threads | 9:16, 1:1, 4:5, 16:9 | 9:16 | 5 min | 1 GB |
| Pinterest | 9:16, 1:1, 4:5 | 9:16 | 15 min | 2 GB |
| Snapchat Spotlight | 9:16 | 9:16 | 3 min | 1 GB |

Free-tier limits throughout. X in particular raises both length and size
substantially on paid tiers.

## Safe areas

Separately from what a platform will *accept*, each one covers part of the frame
with its own interface. `PLATFORM_SAFE_AREAS` in `scene.py` holds those bands and
the editor draws them; `platforms.py` points each destination at the right one.
This is why the default layout stops the waveform at 80% of a vertical frame.

## Keeping it current

`test_platforms.py` asserts the table is internally consistent — every preferred
ratio is one the platform accepts, every ratio is one this app can render, every
safe-area key exists — but it cannot know whether TikTok changed its cap last
week. When a limit moves, edit the entry and bump its `checked` date.
