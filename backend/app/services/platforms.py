"""What each platform will actually accept.

An export that is thirty seconds too long, or six megabytes over a cap, fails at
the upload step — after the render, after the wait, usually on a phone. Knowing
the constraints up front turns that into something the editor can say before you
spend the GPU time.

**These numbers change.** Platforms revise duration caps and file limits without
notice, and several differ between the mobile app and the web uploader. Every
entry carries a `checked` date and a `notes` line; treat this as a table to
maintain, not a constant. Where a limit is disputed between the app and the web,
the *lower* value is used, because a spec that says yes and an upload that says
no is worse than a spec that is slightly cautious.

Verified against each platform's published creator documentation in May 2026.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MB = 1024 * 1024
GB = 1024 * MB


@dataclass(frozen=True)
class Platform:
    """One upload destination and what it will take."""

    key: str
    label: str
    # Aspect ratios that upload without being letterboxed or cropped.
    ratios: tuple[str, ...]
    # The shape this destination is really designed around.
    preferred_ratio: str
    min_seconds: float
    max_seconds: float
    max_bytes: int
    containers: tuple[str, ...]
    video_codecs: tuple[str, ...]
    audio_codecs: tuple[str, ...]
    # Recommended ceiling for the video bitrate, in bits per second. Going above
    # it buys nothing: every platform re-encodes on ingest.
    max_video_bitrate: int
    frame_rates: tuple[int, ...]
    # Which safe-area guide in scene.PLATFORM_SAFE_AREAS applies, if any.
    safe_area: str | None
    checked: str
    notes: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)


# Ordered roughly by how much podcast clips are posted to them.
PLATFORMS: tuple[Platform, ...] = (
    Platform(
        key="tiktok",
        label="TikTok",
        ratios=("9:16", "1:1", "16:9"),
        preferred_ratio="9:16",
        min_seconds=3.0,
        max_seconds=600.0,
        # The web uploader accepts far more; the app is the tighter of the two.
        max_bytes=500 * MB,
        containers=("mp4", "mov", "webm"),
        video_codecs=("h264", "hevc"),
        audio_codecs=("aac",),
        max_video_bitrate=10_000_000,
        frame_rates=(24, 25, 30, 60),
        safe_area="tiktok",
        checked="2026-05",
        notes="Caption and UI chrome cover roughly the bottom fifth and the "
              "right edge. Minimum 540x960.",
    ),
    Platform(
        key="reels",
        label="Instagram Reels",
        ratios=("9:16",),
        preferred_ratio="9:16",
        min_seconds=3.0,
        max_seconds=180.0,
        max_bytes=4 * GB,
        containers=("mp4", "mov"),
        video_codecs=("h264", "hevc"),
        audio_codecs=("aac",),
        max_video_bitrate=25_000_000,
        frame_rates=(24, 25, 30, 60),
        safe_area="reels",
        checked="2026-05",
        notes="Anything not 9:16 is cropped or padded. 1080x1920 is the target.",
    ),
    Platform(
        key="instagram_feed",
        label="Instagram Feed",
        ratios=("4:5", "1:1", "16:9"),
        preferred_ratio="4:5",
        min_seconds=3.0,
        max_seconds=3600.0,
        max_bytes=4 * GB,
        containers=("mp4", "mov"),
        video_codecs=("h264", "hevc"),
        audio_codecs=("aac",),
        max_video_bitrate=25_000_000,
        frame_rates=(24, 25, 30, 60),
        safe_area="feed",
        checked="2026-05",
        notes="4:5 takes the most vertical space in the feed without being a Reel.",
    ),
    Platform(
        key="stories",
        label="Instagram / Facebook Stories",
        ratios=("9:16",),
        preferred_ratio="9:16",
        min_seconds=3.0,
        # Longer uploads are split into 60-second cards.
        max_seconds=60.0,
        max_bytes=4 * GB,
        containers=("mp4", "mov"),
        video_codecs=("h264", "hevc"),
        audio_codecs=("aac",),
        max_video_bitrate=25_000_000,
        frame_rates=(24, 25, 30, 60),
        safe_area="reels",
        checked="2026-05",
        notes="Anything longer is cut into 60-second cards on upload.",
    ),
    Platform(
        key="shorts",
        label="YouTube Shorts",
        ratios=("9:16",),
        preferred_ratio="9:16",
        min_seconds=1.0,
        max_seconds=180.0,
        max_bytes=2 * GB,
        containers=("mp4", "mov", "webm"),
        video_codecs=("h264", "hevc", "vp9", "av1"),
        audio_codecs=("aac", "opus"),
        max_video_bitrate=20_000_000,
        frame_rates=(24, 25, 30, 60),
        safe_area="shorts",
        checked="2026-05",
        notes="Over three minutes it is published as a normal video, not a Short.",
    ),
    Platform(
        key="youtube",
        label="YouTube",
        ratios=("16:9", "9:16", "1:1", "4:5"),
        preferred_ratio="16:9",
        min_seconds=1.0,
        max_seconds=43200.0,
        max_bytes=256 * GB,
        containers=("mp4", "mov", "webm", "mkv"),
        video_codecs=("h264", "hevc", "vp9", "av1"),
        audio_codecs=("aac", "opus", "flac"),
        max_video_bitrate=16_000_000,
        frame_rates=(24, 25, 30, 48, 50, 60),
        safe_area=None,
        checked="2026-05",
        notes="Twelve hours and 256GB are the verified-account limits.",
    ),
    Platform(
        key="facebook_reels",
        label="Facebook Reels",
        ratios=("9:16",),
        preferred_ratio="9:16",
        min_seconds=3.0,
        max_seconds=90.0,
        max_bytes=4 * GB,
        containers=("mp4", "mov"),
        video_codecs=("h264", "hevc"),
        audio_codecs=("aac",),
        max_video_bitrate=25_000_000,
        frame_rates=(24, 25, 30, 60),
        safe_area="reels",
        checked="2026-05",
    ),
    Platform(
        key="facebook_feed",
        label="Facebook Feed",
        ratios=("16:9", "1:1", "4:5", "9:16"),
        preferred_ratio="1:1",
        min_seconds=1.0,
        max_seconds=14400.0,
        max_bytes=10 * GB,
        containers=("mp4", "mov"),
        video_codecs=("h264", "hevc"),
        audio_codecs=("aac",),
        max_video_bitrate=25_000_000,
        frame_rates=(24, 25, 30, 60),
        safe_area="feed",
        checked="2026-05",
    ),
    Platform(
        key="linkedin",
        label="LinkedIn",
        ratios=("16:9", "1:1", "4:5", "9:16"),
        preferred_ratio="1:1",
        min_seconds=3.0,
        max_seconds=600.0,
        max_bytes=5 * GB,
        containers=("mp4", "mov"),
        video_codecs=("h264",),
        audio_codecs=("aac",),
        max_video_bitrate=10_000_000,
        frame_rates=(24, 25, 30, 60),
        safe_area="feed",
        checked="2026-05",
        notes="Square performs best in the feed; sound is off by default, so "
              "captions matter more here than anywhere.",
    ),
    Platform(
        key="x",
        label="X / Twitter",
        ratios=("16:9", "1:1", "9:16"),
        preferred_ratio="16:9",
        min_seconds=0.5,
        # 2:20 on a free account. Premium tiers allow far longer.
        max_seconds=140.0,
        max_bytes=512 * MB,
        containers=("mp4", "mov"),
        video_codecs=("h264",),
        audio_codecs=("aac",),
        max_video_bitrate=25_000_000,
        frame_rates=(24, 25, 30, 40, 50, 60),
        safe_area=None,
        checked="2026-05",
        notes="140 seconds and 512MB are the free-account limits; paid tiers "
              "raise both substantially.",
        aliases=("twitter",),
    ),
    Platform(
        key="threads",
        label="Threads",
        ratios=("9:16", "1:1", "4:5", "16:9"),
        preferred_ratio="9:16",
        min_seconds=1.0,
        max_seconds=300.0,
        max_bytes=1 * GB,
        containers=("mp4", "mov"),
        video_codecs=("h264", "hevc"),
        audio_codecs=("aac",),
        max_video_bitrate=25_000_000,
        frame_rates=(24, 25, 30, 60),
        safe_area="feed",
        checked="2026-05",
    ),
    Platform(
        key="pinterest",
        label="Pinterest",
        ratios=("9:16", "1:1", "4:5"),
        preferred_ratio="9:16",
        min_seconds=4.0,
        max_seconds=900.0,
        max_bytes=2 * GB,
        containers=("mp4", "mov", "m4v"),
        video_codecs=("h264", "hevc"),
        audio_codecs=("aac",),
        max_video_bitrate=25_000_000,
        frame_rates=(24, 25, 30),
        safe_area="feed",
        checked="2026-05",
    ),
    Platform(
        key="snapchat",
        label="Snapchat Spotlight",
        ratios=("9:16",),
        preferred_ratio="9:16",
        min_seconds=5.0,
        max_seconds=180.0,
        max_bytes=1 * GB,
        containers=("mp4", "mov"),
        video_codecs=("h264",),
        audio_codecs=("aac",),
        max_video_bitrate=20_000_000,
        frame_rates=(24, 25, 30),
        safe_area="tiktok",
        checked="2026-05",
        notes="Spotlight needs sound and is strict about 9:16.",
    ),
)

BY_KEY = {platform.key: platform for platform in PLATFORMS}
for _platform in PLATFORMS:
    for _alias in _platform.aliases:
        BY_KEY[_alias] = _platform


def get(key: str) -> Platform | None:
    return BY_KEY.get(key.strip().lower())


# What this app actually produces, so the codec and container checks mean
# something rather than being asserted against nothing.
OUTPUT_CONTAINER = "mp4"
OUTPUT_VIDEO_CODEC = "h264"
OUTPUT_AUDIO_CODEC = "aac"


@dataclass
class Verdict:
    """Whether one clip can go to one platform, and what is wrong if not."""

    platform: str
    label: str
    ok: bool
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "platform": self.platform,
            "label": self.label,
            "ok": self.ok,
            "blocking": self.blocking,
            "warnings": self.warnings,
        }


def _duration_text(seconds: float) -> str:
    if seconds >= 3600:
        return f"{seconds / 3600:.0f}h"
    if seconds >= 60:
        return f"{seconds / 60:.0f} min"
    return f"{seconds:.0f}s"


def _size_text(size_bytes: int) -> str:
    if size_bytes >= GB:
        return f"{size_bytes / GB:.0f}GB"
    return f"{size_bytes / MB:.0f}MB"


def check(
    platform: Platform,
    aspect_ratio: str,
    duration: float,
    file_bytes: int | None = None,
    container: str = OUTPUT_CONTAINER,
    video_codec: str = OUTPUT_VIDEO_CODEC,
    audio_codec: str = OUTPUT_AUDIO_CODEC,
) -> Verdict:
    """Whether a clip meets one platform's requirements.

    Blocking problems will fail the upload. Warnings will succeed but produce a
    worse post — the wrong shape gets cropped or padded, which on a vertical
    feed means bars where the picture should be.
    """
    verdict = Verdict(platform=platform.key, label=platform.label, ok=True)

    if duration < platform.min_seconds:
        verdict.blocking.append(
            f"Too short — {platform.label} needs at least "
            f"{_duration_text(platform.min_seconds)}."
        )
    if duration > platform.max_seconds:
        verdict.blocking.append(
            f"Too long — {platform.label} allows "
            f"{_duration_text(platform.max_seconds)}."
        )
    if file_bytes is not None and file_bytes > platform.max_bytes:
        verdict.blocking.append(
            f"Too large — {_size_text(file_bytes)} against a "
            f"{_size_text(platform.max_bytes)} limit."
        )
    if container.lower() not in platform.containers:
        verdict.blocking.append(
            f"{container.upper()} is not accepted; use "
            f"{' or '.join(c.upper() for c in platform.containers)}."
        )
    if video_codec.lower() not in platform.video_codecs:
        verdict.blocking.append(f"{video_codec} video is not accepted.")
    if audio_codec.lower() not in platform.audio_codecs:
        verdict.blocking.append(f"{audio_codec} audio is not accepted.")

    if aspect_ratio not in platform.ratios:
        verdict.blocking.append(
            f"{aspect_ratio} is not supported — use "
            f"{' or '.join(platform.ratios)}."
        )
    elif aspect_ratio != platform.preferred_ratio:
        verdict.warnings.append(
            f"{platform.preferred_ratio} performs better here than {aspect_ratio}."
        )

    verdict.ok = not verdict.blocking
    return verdict


def check_all(
    aspect_ratio: str,
    duration: float,
    file_bytes: int | None = None,
    **kwargs,
) -> list[dict]:
    """Every destination, ready first, so the editor can say where a clip can go."""
    verdicts = [
        check(platform, aspect_ratio, duration, file_bytes, **kwargs)
        for platform in PLATFORMS
    ]
    verdicts.sort(key=lambda item: (not item.ok, bool(item.warnings), item.label))
    return [verdict.as_dict() for verdict in verdicts]


def destinations_for(aspect_ratio: str) -> list[Platform]:
    """Platforms that take this shape at all, best fit first."""
    matching = [p for p in PLATFORMS if aspect_ratio in p.ratios]
    matching.sort(key=lambda p: (p.preferred_ratio != aspect_ratio, p.label))
    return matching


def as_dict(platform: Platform) -> dict:
    return {
        "key": platform.key,
        "label": platform.label,
        "ratios": list(platform.ratios),
        "preferred_ratio": platform.preferred_ratio,
        "min_seconds": platform.min_seconds,
        "max_seconds": platform.max_seconds,
        "max_bytes": platform.max_bytes,
        "containers": list(platform.containers),
        "video_codecs": list(platform.video_codecs),
        "audio_codecs": list(platform.audio_codecs),
        "max_video_bitrate": platform.max_video_bitrate,
        "frame_rates": list(platform.frame_rates),
        "safe_area": platform.safe_area,
        "checked": platform.checked,
        "notes": platform.notes,
    }
