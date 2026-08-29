from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    complete = "complete"
    failed = "failed"
    canceled = "canceled"


class JobKind(str, enum.Enum):
    check_feeds = "check_feeds"
    import_episode = "import_episode"
    analyze_media = "analyze_media"
    waveform = "waveform"
    transcribe = "transcribe"
    render = "render"
    model_download = "model_download"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    # Username, not email. This is a self-hosted box for a small number of
    # people; an address adds a field to type and a thing to verify without
    # buying anything, since nothing here ever sends mail.
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)


class SessionToken(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(default=now_utc)

    user: Mapped[User] = relationship()


class AppSetting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    original_name: Mapped[str] = mapped_column(String(512))
    stored_name: Mapped[str] = mapped_column(String(512), unique=True)
    content_type: Mapped[str] = mapped_column(String(256), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    probe_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Peak envelope for the editor's waveform; see app.services.waveform.
    peaks_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)

    owner: Mapped[User] = relationship()


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    media_id: Mapped[str | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(256))
    clip_start: Mapped[float] = mapped_column(Float, default=0.0)
    clip_end: Mapped[float] = mapped_column(Float, default=45.0)
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="9:16")
    scene_json: Mapped[str] = mapped_column(Text, default="{}")

    # Where this clip came from, and whether a person has looked at it.
    #
    # Clips somebody made are `approved` from the moment they exist: they were
    # already a decision. Clips a watched feed cut while nobody was looking are
    # `pending` until seen, which is the whole reason automation like this is
    # tolerable — the machine proposes, a person disposes.
    source: Mapped[str] = mapped_column(String(16), default="manual")
    review_state: Mapped[str] = mapped_column(String(16), default="approved")

    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)

    owner: Mapped[User] = relationship()
    media: Mapped[MediaAsset | None] = relationship()


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[JobKind] = mapped_column(Enum(JobKind))
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.queued, index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    subject_id: Mapped[str | None] = mapped_column(String, nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What this job was rendering, as a digest of everything that decides the
    # output. Lets an identical render be recognised rather than repeated.
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)

    owner: Mapped[User] = relationship()



class SoundKind(str, enum.Enum):
    music = "music"
    sfx = "sfx"


class SoundAsset(Base):
    """A track or effect from an imported, licensed sound pack.

    Rows are derived state: the importer rebuilds them from the files on disk
    under ``settings.library_dir``. Nothing here is owner-scoped because the
    library is installation-wide, not per-user.
    """

    __tablename__ = "sound_assets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    kind: Mapped[SoundKind] = mapped_column(Enum(SoundKind), index=True)
    pack: Mapped[str] = mapped_column(String(128), index=True)
    relative_path: Mapped[str] = mapped_column(String(512), unique=True)
    title: Mapped[str] = mapped_column(String(256))
    author: Mapped[str] = mapped_column(String(256))
    attribution: Mapped[str] = mapped_column(String(512))
    license_name: Mapped[str] = mapped_column(String(128))
    redistributable: Mapped[bool] = mapped_column(Boolean, default=False)
    genre: Mapped[str] = mapped_column(String(128), default="", index=True)
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    seamless_loop: Mapped[bool] = mapped_column(Boolean, default=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)


class Template(Base):
    """A saved design, reusable across episodes.

    Stores a scene without its media: the layout, colours, wave style, caption
    preset and layer geometry are what recur every week, while the audio and
    the artwork are what change. Applying one therefore leaves the project's
    source, clip range and artwork references alone.
    """

    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128))
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="9:16")
    scene_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)

    owner: Mapped[User] = relationship()


class Feed(Base):
    """A podcast feed watched for new episodes.

    The conditional-GET fields are not an optimisation: polling a feed every
    quarter of an hour without them re-downloads an entire episode list each
    time, and podcast hosts are entitled to rate-limit that.
    """

    __tablename__ = "feeds"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(String(1024))
    title: Mapped[str] = mapped_column(String(200), default="Podcast")

    # Where we got to. `last_guid` is the episode identity the feed itself
    # publishes, which is more reliable than dates: plenty of feeds backfill.
    last_guid: Mapped[str | None] = mapped_column(String(512), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_checked: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # What to do with a new episode.
    active: Mapped[bool] = mapped_column(default=True)
    # Clips to cut automatically. 0 imports and transcribes only.
    clip_count: Mapped[int] = mapped_column(default=0)
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="9:16")
    template_id: Mapped[str | None] = mapped_column(
        ForeignKey("templates.id", ondelete="SET NULL"), nullable=True
    )
    # Rendering without being asked is the one thing creators are frightened
    # of, so clips are prepared and left for a person to approve.
    auto_render: Mapped[bool] = mapped_column(default=False)

    created_at: Mapped[datetime] = mapped_column(default=now_utc)

    owner: Mapped[User] = relationship()


class ProjectRevision(Base):
    """How a project was, before something changed it.

    Applying a template, cutting words out of the transcript, or running a
    batch each rewrite a clip in one click. Each is worth having and none is
    comfortable without a way back.
    """

    __tablename__ = "project_revisions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # What the change was, in the words somebody would use about it.
    label: Mapped[str] = mapped_column(String(80), default="Edited")
    # Of the state, so the same state is never stored twice.
    digest: Mapped[str] = mapped_column(String(32), default="")
    state_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(default=now_utc)


class FeedEpisode(Base):
    """An episode seen in a feed, so it is never imported twice."""

    __tablename__ = "feed_episodes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    feed_id: Mapped[str] = mapped_column(ForeignKey("feeds.id", ondelete="CASCADE"))
    guid: Mapped[str] = mapped_column(String(512))
    title: Mapped[str] = mapped_column(String(400), default="Episode")
    published: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enclosure_url: Mapped[str] = mapped_column(String(2048), default="")
    media_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Moments the podcaster marked with <podcast:soundbite>, stored when the
    # feed is read: by the time the audio has downloaded and transcribed, the
    # publisher may have edited the feed, and these are worth more than any
    # clip this application can pick on its own.
    soundbites_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
