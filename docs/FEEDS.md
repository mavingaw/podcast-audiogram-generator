# Watching podcast feeds

Add your show's RSS URL and new episodes look after themselves: downloaded,
analysed, transcribed, and — if you ask for it — cut into clips waiting for you.

Verified end to end against a real public podcast: the feed was read, one
episode downloaded (3MB, 2m46s), and analysis, waveform and transcription all
completed without intervention.

## What it will and will not do

**It will not post anything.** Ever. There is no publishing integration and this
does not queue one. Clips land in your library like any others.

**It will not render without being asked.** Rendering is opt-in per feed
(`auto_render`), off by default. Preparing a clip is cheap and reversible;
spending GPU time on twelve exports nobody asked for is neither.

**It will not import a back catalogue.** On first sight of a feed only the
newest episode is taken (`PAS_FEED_FIRST_RUN`). Subscribing to a show with 400
back-episodes should not enqueue 400 transcriptions.

**It will not import the same episode twice.** Identity comes from the feed's
own GUID, not from a date or a title: feeds backfill, and titles get typo-fixed.
`test_checking_queues_new_episodes_once` pins this, because getting it wrong
unattended means a library full of duplicates.

## Being a good citizen

- Conditional GETs, using the `etag` and modified date the host gave us. A 304
  means nothing is re-downloaded.
- A feed is re-read at most every 15 minutes (`PAS_FEED_INTERVAL_MINUTES`), which
  is far finer than any release schedule needs.
- Episodes are capped at 2GB (`PAS_FEED_MAX_BYTES`), enforced against both the
  declared length and what actually arrives, because hosts do not always declare
  one.
- Feed URLs go through the same SSRF checks as the RSS preview: no private
  addresses, no credentials in the URL, HTTP(S) only.
- A feed that fails records the error against itself and is not retried in a
  tight loop.

## The lane

Feed work gets its own worker lane. Reading a feed is quick, but downloading an
episode is minutes of somebody else's bandwidth, and neither should sit in front
of a render.

## Settings per feed

| Setting | Default | What it does |
|---|---|---|
| Clips per episode | 0 | 0 imports and transcribes only. Above that, cuts clips automatically. |
| Look | Default | A saved template applied to every clip, so a batch comes out on-brand. |
| Shape | 9:16 | Aspect ratio for the clips. |
| Render them too | off | Whether to export, or just prepare. |

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `PAS_FEED_POLLING` | `true` | The background schedule. Does nothing until a feed is added. |
| `PAS_FEED_INTERVAL_SECONDS` | `900` | How often the schedule queues a check. |
| `PAS_FEED_INTERVAL_MINUTES` | `15` | Minimum age before a feed is re-read. |
| `PAS_FEED_FIRST_RUN` | `1` | Episodes taken on first sight of a feed. |
| `PAS_FEED_MAX_BYTES` | 2GB | Largest episode that will be downloaded. |
| `PAS_FEED_TIMEOUT` | `900` | Seconds allowed for one episode download. |

## The inbox

Clips a feed cut arrive marked `pending` and appear under **Inbox**. Keeping one
approves it and renders it if it has not been rendered; discarding removes it
and cancels any work already in flight, because rendering something somebody
just threw away is wasted GPU time.

Rejected clips are deleted rather than remembered as rejected. An inbox that
fills with things you already said no to is one you stop opening — and because
the batcher only skips moments that are still *projects*, a discarded moment can
be suggested again later with a different cut.

Clips you made yourself never appear here. They were already a decision.

## What is still missing

A notification. The inbox shows what is waiting, but nothing tells you it is
there unless you look. Email or a push would close that, and the place to add it
is the end of `_import_episode`.
