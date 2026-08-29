from __future__ import annotations

import io
import json
import secrets
import shutil
import time
import zipfile
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter, Cookie, Depends, HTTPException, Request, Response, UploadFile,
)
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.deps import current_admin, current_user
from app.core.config import settings
from app.db.models import (
    AppSetting, Feed, FeedEpisode, Job, JobKind, JobStatus, MediaAsset, Project,
    SoundAsset, Template, User,
)
from app.db.session import SessionLocal, get_db
from app.services.auth import create_session, delete_session, hash_password, verify_password
from app.services import cancellation
from app.services.encoders import describe as describe_encoder
from app.services.gpu import discover_gpus
from app.services.transcription import MODEL_SIZES as WHISPER_MODEL_SIZES
from app.services.transcription import available as transcription_installed
from app.services.transcription import choose_runtime as choose_transcription_runtime
from app.services.library import (
    SFX_ROLES,
    genres as library_genres,
    installed_packs,
    query_sounds,
    serialize_sound,
    sound_path,
    sync_catalog,
)
from app.services.jobs import start_worker_once
from app.services.rss import RssFetchError, parse_feed_url
from app.services.templates import apply_template, scene_for_template
from app.services.variants import RATIO_DIMENSIONS, RATIO_PRESETS, remap_scene, variant_title
from app.services import diarization, llm, speakers, throttle
from app.services.batching import BatchError, make_clips
from app.services.clipfinder import find as find_clips
from app.services.fingerprint import fingerprint as fingerprint_project
from app.services.snapping import snap as snap_clip_range
from app.services.platforms import PLATFORMS, as_dict as platform_as_dict
from app.services.platforms import check_all as check_platforms
from app.services.waveform import MAX_BUCKETS as MAX_PEAK_BUCKETS
from app.services.waveform import decode as decode_peaks
from app.services.waveform import duration_of as waveform_duration
from app.services.waveform import resample as resample_peaks
from app.services.storage import UploadValidationError, contained_path, is_image, save_upload

router = APIRouter(prefix="/api")


# Letters, digits, and the three separators people actually use. Kept
# deliberately narrow: a username ends up in a URL, a log line, and a filename,
# and every character class you allow is one you have to escape somewhere.
USERNAME_PATTERN = r"^[a-z0-9][a-z0-9._-]{2,31}$"
MIN_PASSWORD_LENGTH = 10


def normalise_username(value: str) -> str:
    """Usernames are case-insensitive: `Mujin` and `mujin` are one account."""
    return value.strip().lower()


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)

    @field_validator("username", mode="before")
    @classmethod
    def _fold_case(cls, value: object) -> object:
        return normalise_username(value) if isinstance(value, str) else value


class BootstrapRequest(Credentials):
    pass


class RegisterRequest(Credentials):
    # An invite code, when the instance requires one. Sharing a code with the
    # people you want on the box is the middle ground between issuing accounts
    # by hand and leaving registration open to whoever finds the address.
    code: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str

    @field_validator("username", mode="before")
    @classmethod
    def _fold_case(cls, value: object) -> object:
        return normalise_username(value) if isinstance(value, str) else value


class UserCreate(Credentials):
    is_admin: bool = False


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=10)
    is_admin: bool | None = None
    disabled: bool | None = None


class ProjectCreate(BaseModel):
    media_id: str | None = None
    title: str


class TranscriptUpdate(BaseModel):
    transcript: dict


class ProjectUpdate(BaseModel):
    title: str | None = None
    clip_start: float | None = None
    clip_end: float | None = None
    aspect_ratio: str | None = None
    scene: dict | None = None


class GpuSettings(BaseModel):
    transcription_gpu_uuid: str | None = None
    encoding_gpu_uuid: str | None = None


class RssPreviewRequest(BaseModel):
    url: str


def set_session_cookie(response: Response, token: str, request: Request | None = None) -> None:
    """Issue the session cookie with the same life as the server-side session.

    Without max_age the browser drops it when the window closes, which logs
    people out for no reason; with a longer one the cookie would outlive the
    token it names.

    `secure` is decided from the request rather than hard-coded: forcing it on
    would break sign-in over plain HTTP on the LAN, and leaving it off would
    send the session in clear text once the app is behind a tunnel. Cloudflare
    terminates TLS and tells us so with `x-forwarded-proto`.
    """
    configured = settings.cookie_secure
    if configured in {"1", "true", "yes", "on"}:
        secure = True
    elif configured in {"0", "false", "no", "off"}:
        secure = False
    else:
        proto = ""
        if request is not None:
            proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").lower()
        secure = proto == "https"

    response.set_cookie(
        settings.session_cookie,
        token,
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=settings.session_days * 24 * 60 * 60,
    )


def serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "is_admin": user.is_admin,
        "disabled": user.disabled,
        "created_at": user.created_at.isoformat(),
    }


def serialize_media(media: MediaAsset) -> dict:
    return {
        "has_waveform": bool(media.peaks_json),
        "id": media.id,
        "original_name": media.original_name,
        "content_type": media.content_type,
        "size_bytes": media.size_bytes,
        "duration_seconds": media.duration_seconds,
        "created_at": media.created_at.isoformat(),
        "has_transcript": bool(media.transcript_json),
        "transcript": json.loads(media.transcript_json) if media.transcript_json else None,
    }


def serialize_project(project: Project) -> dict:
    return {
        "id": project.id,
        "media_id": project.media_id,
        "title": project.title,
        "clip_start": project.clip_start,
        "clip_end": project.clip_end,
        "aspect_ratio": project.aspect_ratio,
        "scene": json.loads(project.scene_json or "{}"),
        "source": project.source,
        "review_state": project.review_state,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }


def serialize_job(job: Job) -> dict:
    return {
        "id": job.id,
        "kind": job.kind.value,
        "status": job.status.value,
        "progress": job.progress,
        "subject_id": job.subject_id,
        "message": job.message,
        "error": job.error,
        "result": json.loads(job.result_json) if job.result_json else None,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }


@router.get("/health")
def health() -> dict:
    return {"ok": True, "app": settings.app_name}


@router.get("/bootstrap")
def bootstrap_state(db: Annotated[Session, Depends(get_db)]) -> dict:
    user_count = db.scalar(select(func.count(User.id)))
    return {"initialized": bool(user_count)}


@router.post("/bootstrap")
def bootstrap(payload: BootstrapRequest, response: Response, db: Annotated[Session, Depends(get_db)]) -> dict:
    user_count = db.scalar(select(func.count(User.id)))
    if user_count:
        raise HTTPException(status_code=409, detail="Application is already initialized")
    user = User(username=payload.username, password_hash=hash_password(payload.password), is_admin=True)
    db.add(user)
    db.commit()
    token = create_session(db, user)
    set_session_cookie(response, token)
    return {"user": serialize_user(user)}


@router.post("/auth/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    # Once this is reachable from the internet the sign-in form is the whole
    # security boundary, and an unthrottled one can be guessed at as fast as the
    # network allows.
    key = throttle.key_for(request, payload.username)
    wait = throttle.retry_after(key)
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Too many attempts. Try again in {int(wait) + 1} seconds.",
            headers={"Retry-After": str(int(wait) + 1)},
        )

    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or user.disabled or not verify_password(payload.password, user.password_hash):
        throttle.record_failure(key)
        # One message for both cases: saying which half was wrong tells an
        # attacker whether a username exists.
        raise HTTPException(status_code=401, detail="Invalid username or password")

    throttle.record_success(key)
    token = create_session(db, user)
    set_session_cookie(response, token, request)
    return {"user": serialize_user(user)}


@router.post("/auth/logout")
def logout(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    pas_session: str | None = Cookie(default=None, alias=settings.session_cookie),
) -> dict:
    delete_session(db, pas_session)
    response.delete_cookie(settings.session_cookie)
    return {"ok": True}


SIGNUP_SETTING = "signups_open"


def signups_open(db: Session) -> bool:
    """Whether anyone may create their own account without a code.

    Defaults to *closed*. It used to default to open, which was reasonable while
    this only answered on a LAN and became dangerous the moment it could be put
    behind a tunnel: a public sign-up form on a reachable address means anybody
    with the URL has an account.

    An admin can open it, or — better — set a sign-up code and share that.
    """
    setting = db.get(AppSetting, SIGNUP_SETTING)
    if setting is not None:
        return setting.value == "true"
    return settings.open_signups


def signup_code_required() -> str | None:
    """The code registration needs, if one is configured."""
    code = (settings.signup_code or "").strip()
    return code or None


@router.get("/auth/signup")
def signup_state(db: Annotated[Session, Depends(get_db)]) -> dict:
    """Lets the sign-in screen decide whether to offer a "create account" tab."""
    return {"open": signups_open(db), "code_required": bool(signup_code_required())}


@router.post("/auth/register")
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Create an account and sign in with it.

    Deliberately not admin-gated: that is what makes it sign-up rather than a
    second spelling of `POST /users`. New accounts are never administrators.
    """
    if not db.scalar(select(func.count(User.id))):
        raise HTTPException(
            status_code=409,
            detail="This instance has no administrator yet. Create the first account instead.",
        )
    required = signup_code_required()
    if required:
        supplied = (payload.code or "").strip()
        # Compared in constant time: a code is a shared secret, and a timing
        # difference on a reachable address is a way to guess it.
        if not secrets.compare_digest(supplied, required):
            throttle.record_failure(throttle.key_for(request, "signup"))
            raise HTTPException(status_code=403, detail="That sign-up code is not valid")
    elif not signups_open(db):
        raise HTTPException(
            status_code=403,
            detail="Sign-ups are closed on this instance. Ask the owner for an account.",
        )
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status_code=409, detail="That username is taken")

    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        is_admin=False,
    )
    db.add(user)
    db.commit()
    set_session_cookie(response, create_session(db, user), request)
    return {"user": serialize_user(user)}


@router.put("/settings/signups")
def set_signups(
    payload: dict,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(current_admin)],
) -> dict:
    setting = db.get(AppSetting, SIGNUP_SETTING) or AppSetting(key=SIGNUP_SETTING, value="")
    setting.value = "true" if payload.get("open") else "false"
    db.merge(setting)
    db.commit()
    return {"open": signups_open(db)}


@router.get("/me")
def me(user: Annotated[User, Depends(current_user)]) -> dict:
    return {"user": serialize_user(user)}


@router.get("/users")
def list_users(db: Annotated[Session, Depends(get_db)], _: Annotated[User, Depends(current_admin)]) -> dict:
    users = db.scalars(select(User).order_by(User.created_at.asc())).all()
    return {"users": [serialize_user(user) for user in users]}


@router.post("/users")
def create_user(
    payload: UserCreate,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(current_admin)],
) -> dict:
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status_code=409, detail="That username is taken")
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        is_admin=payload.is_admin,
    )
    db.add(user)
    db.commit()
    return {"user": serialize_user(user)}


@router.patch("/users/{user_id}")
def update_user(
    user_id: str,
    payload: UserUpdate,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(current_admin)],
) -> dict:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("disabled") is True and user.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot disable your own account")
    if updates.get("is_admin") is False and user.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot remove your own admin access")
    if "password" in updates and updates["password"]:
        user.password_hash = hash_password(updates.pop("password"))
    for key, value in updates.items():
        setattr(user, key, value)
    db.commit()
    return {"user": serialize_user(user)}


@router.get("/gpus")
def gpus(_: Annotated[User, Depends(current_user)]) -> dict:
    return {"gpus": discover_gpus()}


@router.get("/settings/gpu")
def get_gpu_settings(db: Annotated[Session, Depends(get_db)], _: Annotated[User, Depends(current_user)]) -> dict:
    rows = db.scalars(select(AppSetting).where(AppSetting.key.in_(["transcription_gpu_uuid", "encoding_gpu_uuid"]))).all()
    return {row.key: row.value for row in rows}


@router.put("/settings/gpu")
def set_gpu_settings(
    payload: GpuSettings,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(current_user)],
) -> dict:
    for key, value in payload.model_dump().items():
        setting = db.get(AppSetting, key) or AppSetting(key=key, value="")
        setting.value = value or ""
        db.merge(setting)
    db.commit()
    return {"ok": True}


@router.post("/media/upload")
async def upload_media(
    file: UploadFile,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    try:
        stored_name, size = await save_upload(file)
    except UploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    media = MediaAsset(
        owner_id=user.id,
        original_name=file.filename or "upload.bin",
        stored_name=stored_name,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=size,
    )
    db.add(media)
    db.commit()
    # Images are artwork, not a source track: probing, waveform extraction and
    # transcription all assume an audio stream, and queueing them for a PNG only
    # produces jobs that fail.
    queued: list[Job] = []
    if not is_image(media.original_name):
        queued = [
            Job(owner_id=user.id, kind=JobKind.analyze_media, subject_id=media.id, message="Queued media analysis"),
            Job(owner_id=user.id, kind=JobKind.waveform, subject_id=media.id, message="Queued waveform"),
            Job(owner_id=user.id, kind=JobKind.transcribe, subject_id=media.id, message="Queued transcription"),
        ]
        db.add_all(queued)
    db.commit()
    start_worker_once()
    return {"media": serialize_media(media), "jobs": [serialize_job(job) for job in queued]}


@router.get("/media")
def list_media(db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(current_user)]) -> dict:
    items = db.scalars(select(MediaAsset).where(MediaAsset.owner_id == user.id).order_by(MediaAsset.created_at.desc())).all()
    return {"media": [serialize_media(item) for item in items]}


@router.get("/media/{media_id}/file")
def media_file(media_id: str, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(current_user)]):
    media = db.get(MediaAsset, media_id)
    if not media or media.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Media not found")
    path = contained_path(settings.uploads_dir, settings.uploads_dir / media.stored_name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Media file not found")
    return FileResponse(path, media_type=media.content_type, filename=media.original_name)


class SnapRequest(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)


@router.post("/media/{media_id}/snap")
def snap_clip(
    media_id: str,
    payload: SnapRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Nudge a clip's edges off the middle of a word.

    Cutting mid-word is the most obvious sign a clip was made by dragging a
    handle rather than by listening. The transcript knows where each word began
    and ended, so this is a lookup rather than an analysis pass.
    """
    media = db.get(MediaAsset, media_id)
    if not media or media.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Media not found")
    if payload.end <= payload.start:
        raise HTTPException(status_code=400, detail="end must be greater than start")

    transcript = json.loads(media.transcript_json) if media.transcript_json else None
    return snap_clip_range(
        transcript, payload.start, payload.end, duration=media.duration_seconds
    ).as_dict()


class SpeakerName(BaseModel):
    name: str = Field(max_length=40)


class SpeakerAssignment(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    speaker_id: int = Field(ge=1, le=speakers.MAX_SPEAKERS)


def _load_transcript(media: MediaAsset) -> dict:
    return json.loads(media.transcript_json) if media.transcript_json else {"segments": []}


def _owned_media(db: Session, media_id: str, user: User) -> MediaAsset:
    media = db.get(MediaAsset, media_id)
    if not media or media.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Media not found")
    return media


@router.get("/media/{media_id}/speakers")
def list_speakers(
    media_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Who is in this recording, with the colour their captions are tinted."""
    media = _owned_media(db, media_id, user)
    transcript = _load_transcript(media)
    return {
        "speakers": speakers.summary(transcript),
        "multi": speakers.is_multi_speaker(transcript),
        "detection": diarization.runtime_status(),
    }


@router.post("/media/{media_id}/speakers/{speaker_id}/name")
def rename_speaker(
    media_id: str,
    speaker_id: int,
    payload: SpeakerName,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Give a speaker a real name. It appears wherever the number did."""
    media = _owned_media(db, media_id, user)
    transcript = _load_transcript(media)
    try:
        speakers.rename(transcript, speaker_id, payload.name)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    media.transcript_json = json.dumps(transcript)
    db.commit()
    return {"speakers": speakers.summary(transcript)}


@router.post("/media/{media_id}/speakers/assign")
def assign_speaker(
    media_id: str,
    payload: SpeakerAssignment,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Attribute a stretch of the transcript to one person.

    A range rather than a line, because people talk in turns: correcting one
    sentence and having the rest of the turn follow is what makes fixing an
    hour of audio bearable.
    """
    media = _owned_media(db, media_id, user)
    transcript = _load_transcript(media)
    try:
        changed = speakers.assign(transcript, payload.start, payload.end, payload.speaker_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    media.transcript_json = json.dumps(transcript)
    db.commit()
    return {"changed": changed, "speakers": speakers.summary(transcript)}


class DiarizeRequest(BaseModel):
    # Known counts beat estimated ones by a wide margin; see docs/SPEAKERS.md.
    speaker_count: int | None = Field(default=None, ge=1, le=speakers.MAX_SPEAKERS)


@router.post("/media/{media_id}/speakers/detect")
def detect_speakers(
    media_id: str,
    payload: DiarizeRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Run speaker detection again, optionally told how many people to expect."""
    media = _owned_media(db, media_id, user)
    if not diarization.available():
        raise HTTPException(status_code=503, detail="Speaker detection is not installed")
    if not media.transcript_json:
        raise HTTPException(status_code=400, detail="Transcribe this media first")

    path = contained_path(settings.uploads_dir, settings.uploads_dir / media.stored_name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Source media file is missing")

    transcript = _load_transcript(media)
    try:
        result = diarization.analyse(path, speaker_count=payload.speaker_count)
    except diarization.DiarizationError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    diarization.apply(transcript, result)
    media.transcript_json = json.dumps(transcript)
    db.commit()
    return {
        "speaker_count": result.speaker_count,
        "speakers": speakers.summary(transcript),
    }


class BatchRequest(BaseModel):
    count: int = Field(default=6, ge=1, le=12)
    aspect_ratio: str = "9:16"
    render: bool = True
    # Optional design to apply to every clip, so a batch comes out on-brand.
    template_id: str | None = None


@router.get("/media/{media_id}/exports.zip")
def download_batch(
    media_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
):
    """Every finished clip from one episode, as a single download.

    Ten clips is ten right-clicks otherwise, and the whole point of making them
    in a batch is not handling them one at a time afterwards.

    Streamed rather than assembled in memory: a dozen 1080p clips is comfortably
    more than a container should hold in RAM to answer one request.
    """
    media = _owned_media(db, media_id, user)
    projects = db.scalars(
        select(Project)
        .where(Project.owner_id == user.id, Project.media_id == media.id)
        .order_by(Project.clip_start.asc())
    ).all()

    ready = []
    for project in projects:
        rendered = settings.outputs_dir / project.id / "audiogram.mp4"
        if rendered.exists():
            ready.append((project, rendered))
    if not ready:
        raise HTTPException(status_code=404, detail="Nothing has finished rendering yet")

    def stream():
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
            used: set[str] = set()
            for project, path in ready:
                # MP4 is already compressed; ZIP_STORED avoids spending CPU to
                # save nothing.
                safe = "".join(
                    character if character.isalnum() or character in " -_" else "_"
                    for character in project.title
                ).strip() or project.id[:8]
                name = f"{safe}.mp4"
                suffix = 2
                while name in used:
                    name = f"{safe} ({suffix}).mp4"
                    suffix += 1
                used.add(name)
                archive.write(path, arcname=name)
                yield buffer.getvalue()
                buffer.seek(0)
                buffer.truncate(0)
        yield buffer.getvalue()

    stem = "".join(
        character if character.isalnum() or character in " -_" else "_"
        for character in Path(media.original_name).stem
    ).strip() or "clips"
    return StreamingResponse(
        stream(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{stem} clips.zip"'},
    )


@router.post("/media/{media_id}/batch")
def batch_clips(
    media_id: str,
    payload: BatchRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Turn one episode into a set of clips in a single action.

    This is the point of everything upstream of it: the suggestions know where
    the good moments are, snapping keeps the cuts off the middle of words, and
    the render lanes run several at once. Doing that one clip at a time is the
    part of the job that makes people stop bothering.

    The work itself lives in `services/batching` because the feed watcher does
    exactly this without anybody pressing anything, and a clip made
    automatically must be the same clip you would have made by hand.
    """
    media = _owned_media(db, media_id, user)
    if payload.aspect_ratio not in RATIO_DIMENSIONS:
        raise HTTPException(status_code=400, detail="Unknown aspect ratio")
    if payload.template_id:
        template = db.get(Template, payload.template_id)
        if not template or template.owner_id != user.id:
            raise HTTPException(status_code=404, detail="Template not found")

    try:
        created = make_clips(
            db,
            owner_id=user.id,
            media=media,
            count=payload.count,
            aspect_ratio=payload.aspect_ratio,
            template_id=payload.template_id,
            render=payload.render,
        )
    except BatchError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    jobs = []
    if payload.render and created:
        start_worker_once()
        jobs = db.scalars(
            select(Job).where(
                Job.kind == JobKind.render,
                Job.subject_id.in_([item.id for item in created]),
            )
        ).all()

    return {
        "projects": [serialize_project(item) for item in created],
        "jobs": [serialize_job(job) for job in jobs],
        "skipped": max(0, payload.count - len(created)),
    }


@router.get("/media/{media_id}/clips")
def suggest_clips(
    media_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
    limit: int = 8,
) -> dict:
    """Suggest clips worth cutting from this media.

    Scrubbing an episode for the thirty seconds that will travel is the slow
    part of the job. This reads the transcript and the peak envelope that are
    already stored, so it costs a few milliseconds and needs no model.
    """
    media = db.get(MediaAsset, media_id)
    if not media or media.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Media not found")
    if limit < 1 or limit > 25:
        raise HTTPException(status_code=400, detail="limit must be 1..25")

    transcript = json.loads(media.transcript_json) if media.transcript_json else None
    if not transcript or not transcript.get("segments"):
        return {"ready": False, "clips": [],
                "reason": "The transcript is still being prepared."}

    raw = decode_peaks(media.peaks_json) if media.peaks_json else None
    # Stored as bytes; the scorer reasons in 0..1.
    peaks = [value / 255.0 for value in raw] if raw else []
    clips = find_clips(
        transcript,
        peaks=peaks,
        duration=media.duration_seconds or transcript.get("duration"),
        # Ask for a wider net than requested when a model can read them: the
        # heuristics decide what is *shaped* like a clip, the model decides
        # which of those is worth watching, and it needs choices to choose from.
        limit=limit * 2 if llm.available() else limit,
    )
    clips = llm.rerank(clips)[:limit]
    if not clips:
        return {"ready": True, "clips": [],
                "reason": "No passage in this audio is both long enough and "
                          "self-contained enough to suggest."}
    return {"ready": True, "clips": clips}


@router.get("/media/{media_id}/peaks")
def media_peaks(
    media_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
    buckets: int = 600,
    start: float | None = None,
    end: float | None = None,
) -> dict:
    """Peak envelope for the media, reduced to the caller's display width.

    Returns an empty list rather than a 404 while the waveform job is still
    running, so the editor can draw a placeholder and poll instead of treating
    a pending waveform as an error.
    """
    media = db.get(MediaAsset, media_id)
    if not media or media.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Media not found")
    if buckets < 1 or buckets > MAX_PEAK_BUCKETS:
        raise HTTPException(status_code=400, detail=f"buckets must be 1..{MAX_PEAK_BUCKETS}")
    if start is not None and end is not None and end <= start:
        raise HTTPException(status_code=400, detail="end must be greater than start")
    return {
        "ready": bool(media.peaks_json),
        "duration": waveform_duration(media.peaks_json) or media.duration_seconds,
        "peaks": resample_peaks(media.peaks_json, buckets, start, end),
    }


@router.patch("/media/{media_id}/transcript")
def update_media_transcript(
    media_id: str,
    payload: TranscriptUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    media = db.get(MediaAsset, media_id)
    if not media or media.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Media not found")
    media.transcript_json = json.dumps(payload.transcript)
    db.commit()
    return {"media": serialize_media(media)}


@router.post("/projects")
def create_project(
    payload: ProjectCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    if payload.media_id:
        media = db.get(MediaAsset, payload.media_id)
        if not media or media.owner_id != user.id:
            raise HTTPException(status_code=404, detail="Media not found")
    project = Project(
        owner_id=user.id,
        media_id=payload.media_id,
        title=payload.title,
        scene_json=json.dumps(
            {
                "background": "#101820",
                "accent": "#23a094",
                "captionStyle": "clean",
                "waveform": True,
            }
        ),
    )
    db.add(project)
    db.commit()
    return {"project": serialize_project(project)}


@router.get("/projects")
def list_projects(db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(current_user)]) -> dict:
    projects = db.scalars(select(Project).where(Project.owner_id == user.id).order_by(Project.updated_at.desc())).all()
    return {"projects": [serialize_project(project) for project in projects]}


@router.get("/projects/{project_id}")
def get_project(project_id: str, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(current_user)]) -> dict:
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    media = db.get(MediaAsset, project.media_id) if project.media_id else None
    return {"project": serialize_project(project), "media": serialize_media(media) if media else None}


@router.patch("/projects/{project_id}")
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    updates = payload.model_dump(exclude_unset=True)
    if "scene" in updates:
        project.scene_json = json.dumps(updates.pop("scene"))
    for key, value in updates.items():
        setattr(project, key, value)
    db.commit()
    return {"project": serialize_project(project)}


@router.delete("/projects/{project_id}")
def delete_project(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Remove a project, its queued work, and its rendered output.

    Without this a workspace only ever grows: every trial clip and every
    verification run leaves a project behind and there is no way to tidy up.
    """
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    # Cancel anything still queued or running for it, so a render does not
    # finish and write output for a project that no longer exists.
    for job in db.scalars(
        select(Job).where(
            Job.subject_id == project.id,
            Job.status.in_([JobStatus.queued, JobStatus.running]),
        )
    ).all():
        cancellation.request(job.id)
        job.status = JobStatus.canceled
        job.message = "Project deleted"

    db.execute(delete(Job).where(Job.subject_id == project.id))
    db.delete(project)
    db.commit()

    # Best effort: a leftover directory is clutter, not a failure worth
    # refusing the delete over.
    outputs = settings.outputs_dir / project_id
    if outputs.is_dir():
        shutil.rmtree(outputs, ignore_errors=True)
    return {"ok": True}


@router.post("/projects/{project_id}/render")
def render_project(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
    force: bool = False,
) -> dict:
    """Render a clip, unless the identical render already exists.

    A double-clicked Export, a retry after a timeout that had in fact succeeded,
    a batch overlapping a previous one — each costs a GPU minute to produce a
    file byte-identical to one already on disk. The fingerprint covers exactly
    what decides the output, so a match means the work is genuinely redundant.

    `force=true` renders anyway, for when somebody wants a fresh file.
    """
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    digest = fingerprint_project({
        "media_id": project.media_id,
        "clip_start": project.clip_start,
        "clip_end": project.clip_end,
        "aspect_ratio": project.aspect_ratio,
        "scene": json.loads(project.scene_json or "{}"),
    })

    if not force:
        # Already being rendered: hand back the job in flight rather than
        # starting a second one alongside it.
        running = db.scalar(
            select(Job)
            .where(
                Job.owner_id == user.id,
                Job.kind == JobKind.render,
                Job.subject_id == project.id,
                Job.status.in_([JobStatus.queued, JobStatus.running]),
            )
            .order_by(Job.created_at.desc())
        )
        if running:
            return {"job": serialize_job(running), "reused": True,
                    "reason": "This clip is already rendering."}

        # Already rendered, unchanged, and the file is still there.
        done = db.scalar(
            select(Job)
            .where(
                Job.owner_id == user.id,
                Job.kind == JobKind.render,
                Job.subject_id == project.id,
                Job.status == JobStatus.complete,
                Job.fingerprint == digest,
            )
            .order_by(Job.created_at.desc())
        )
        if done and (settings.outputs_dir / project.id / "audiogram.mp4").exists():
            return {"job": serialize_job(done), "reused": True,
                    "reason": "Nothing has changed since this was last exported."}

    job = Job(
        owner_id=user.id, kind=JobKind.render, subject_id=project.id,
        message="Queued render", fingerprint=digest,
    )
    db.add(job)
    db.commit()
    start_worker_once()
    return {"job": serialize_job(job), "reused": False}


@router.get("/projects/{project_id}/outputs/{filename}")
def download_project_output(
    project_id: str,
    filename: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
):
    allowed = {"audiogram.mp4", "captions.srt", "captions.vtt", "render-manifest.json", "CREDITS.txt"}
    if filename not in allowed:
        raise HTTPException(status_code=404, detail="Output not found")
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    output_path = contained_path(settings.outputs_dir, settings.outputs_dir / project.id / filename)
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="Output not found")
    media_type = {
        ".mp4": "video/mp4",
        ".srt": "application/x-subrip",
        ".vtt": "text/vtt",
        ".json": "application/json",
        ".txt": "text/plain; charset=utf-8",
    }.get(output_path.suffix, "application/octet-stream")
    return FileResponse(output_path, media_type=media_type, filename=filename)


@router.get("/jobs")
def list_jobs(db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(current_user)]) -> dict:
    jobs = db.scalars(select(Job).where(Job.owner_id == user.id).order_by(Job.created_at.desc()).limit(50)).all()
    return {"jobs": [serialize_job(job) for job in jobs]}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(current_user)]) -> dict:
    job = db.get(Job, job_id)
    if not job or job.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job": serialize_job(job)}


# A stream that never ends holds a worker thread and a socket open forever, so
# it closes itself well before a browser would give up on it.
JOB_STREAM_TIMEOUT_SECONDS = 15 * 60
JOB_STREAM_INTERVAL_SECONDS = 1.0


@router.get("/jobs/{job_id}/events")
def job_events(job_id: str, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(current_user)]):
    job = db.get(Job, job_id)
    if not job or job.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")

    def stream():
        deadline = time.monotonic() + JOB_STREAM_TIMEOUT_SECONDS
        last_payload = None
        while True:
            with SessionLocal() as scoped:
                current = scoped.get(Job, job_id)
                if not current:
                    break
                payload = json.dumps(serialize_job(current))
                finished = current.status.value in {"complete", "failed", "canceled"}
            # Re-sending an unchanged payload every second wakes the client for
            # nothing; a comment frame holds the connection open instead.
            if payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
            else:
                yield ": keep-alive\n\n"
            if finished or time.monotonic() >= deadline:
                break
            time.sleep(JOB_STREAM_INTERVAL_SECONDS)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


class FeedCreate(BaseModel):
    url: str = Field(min_length=8, max_length=1024)
    clip_count: int = Field(default=0, ge=0, le=12)
    aspect_ratio: str = "9:16"
    template_id: str | None = None
    auto_render: bool = False


class FeedUpdate(BaseModel):
    active: bool | None = None
    clip_count: int | None = Field(default=None, ge=0, le=12)
    aspect_ratio: str | None = None
    template_id: str | None = None
    auto_render: bool | None = None


def serialize_feed(feed: Feed, episodes: int = 0) -> dict:
    return {
        "id": feed.id,
        "url": feed.url,
        "title": feed.title,
        "active": feed.active,
        "clip_count": feed.clip_count,
        "aspect_ratio": feed.aspect_ratio,
        "template_id": feed.template_id,
        "auto_render": feed.auto_render,
        "last_checked": feed.last_checked.isoformat() if feed.last_checked else None,
        "last_error": feed.last_error,
        "episodes": episodes,
    }


@router.get("/inbox")
def review_inbox(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Clips a feed cut while nobody was looking, waiting to be looked at.

    The machine proposes and a person disposes. Without this the clips land in
    the library and you have to go and find them, which is the difference
    between automation you trust and automation you stop using.
    """
    pending = db.scalars(
        select(Project)
        .where(Project.owner_id == user.id, Project.review_state == "pending")
        .order_by(Project.created_at.desc())
        .limit(60)
    ).all()

    episodes = {}
    for project in pending:
        if project.media_id and project.media_id not in episodes:
            media = db.get(MediaAsset, project.media_id)
            episodes[project.media_id] = media.original_name if media else "Unknown"

    return {
        "count": len(pending),
        "clips": [
            {
                **serialize_project(project),
                "episode": episodes.get(project.media_id, ""),
                "rendered": (settings.outputs_dir / project.id / "audiogram.mp4").exists(),
            }
            for project in pending
        ],
    }


@router.post("/projects/{project_id}/approve")
def approve_clip(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Keep a suggested clip, and render it if it has not been rendered yet."""
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    project.review_state = "approved"
    job = None
    rendered = (settings.outputs_dir / project.id / "audiogram.mp4").exists()
    already_queued = db.scalar(
        select(Job.id).where(
            Job.subject_id == project.id,
            Job.kind == JobKind.render,
            Job.status.in_([JobStatus.queued, JobStatus.running]),
        )
    )
    if not rendered and not already_queued:
        job = Job(
            owner_id=user.id, kind=JobKind.render, subject_id=project.id,
            message="Queued approved clip",
        )
        db.add(job)
    db.commit()
    if job:
        start_worker_once()
    return {
        "project": serialize_project(project),
        "job": serialize_job(job) if job else None,
    }


@router.post("/projects/{project_id}/reject")
def reject_clip(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Throw a suggested clip away.

    Deleted rather than kept as `rejected`: an inbox that fills with things you
    already said no to is an inbox you stop opening. The moment stays skippable
    because the batcher matches on overlap with existing projects — but a
    rejected clip is gone, so the same moment can be suggested again later with
    a different cut.
    """
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    for job in db.scalars(
        select(Job).where(
            Job.subject_id == project.id,
            Job.status.in_([JobStatus.queued, JobStatus.running]),
        )
    ).all():
        cancellation.request(job.id)
        job.status = JobStatus.canceled
        job.message = "Clip rejected"

    db.execute(delete(Job).where(Job.subject_id == project.id))
    db.delete(project)
    db.commit()

    outputs = settings.outputs_dir / project_id
    if outputs.is_dir():
        shutil.rmtree(outputs, ignore_errors=True)
    return {"ok": True}


@router.get("/feeds")
def list_feeds(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    watched = db.scalars(
        select(Feed).where(Feed.owner_id == user.id).order_by(Feed.created_at.desc())
    ).all()
    counts = {
        feed.id: db.scalar(
            select(func.count()).select_from(FeedEpisode).where(FeedEpisode.feed_id == feed.id)
        ) or 0
        for feed in watched
    }
    return {"feeds": [serialize_feed(feed, counts.get(feed.id, 0)) for feed in watched]}


@router.post("/feeds")
def add_feed(
    payload: FeedCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Watch a podcast feed for new episodes.

    The feed is read once here so a typo fails immediately rather than silently
    never producing anything.
    """
    if payload.aspect_ratio not in RATIO_DIMENSIONS:
        raise HTTPException(status_code=400, detail="Unknown aspect ratio")
    if db.scalar(select(Feed).where(Feed.owner_id == user.id, Feed.url == payload.url)):
        raise HTTPException(status_code=409, detail="That feed is already being watched")

    try:
        parsed = parse_feed_url(payload.url)
    except RssFetchError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error

    feed = Feed(
        owner_id=user.id,
        url=payload.url,
        title=str(getattr(parsed.feed, "title", "") or "Podcast")[:200],
        clip_count=payload.clip_count,
        aspect_ratio=payload.aspect_ratio,
        template_id=payload.template_id,
        auto_render=payload.auto_render,
    )
    db.add(feed)
    db.commit()
    return {"feed": serialize_feed(feed)}


@router.patch("/feeds/{feed_id}")
def update_feed(
    feed_id: str,
    payload: FeedUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    feed = db.get(Feed, feed_id)
    if not feed or feed.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Feed not found")
    updates = payload.model_dump(exclude_unset=True)
    if "aspect_ratio" in updates and updates["aspect_ratio"] not in RATIO_DIMENSIONS:
        raise HTTPException(status_code=400, detail="Unknown aspect ratio")
    for key, value in updates.items():
        setattr(feed, key, value)
    db.commit()
    return {"feed": serialize_feed(feed)}


@router.delete("/feeds/{feed_id}")
def delete_feed(
    feed_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Stop watching a feed. Episodes already imported are left alone."""
    feed = db.get(Feed, feed_id)
    if not feed or feed.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Feed not found")
    db.execute(delete(FeedEpisode).where(FeedEpisode.feed_id == feed.id))
    db.delete(feed)
    db.commit()
    return {"ok": True}


@router.post("/feeds/check")
def check_feeds_now(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Read every watched feed now, without waiting for the schedule."""
    job = Job(owner_id=user.id, kind=JobKind.check_feeds, message="Checking feeds")
    db.add(job)
    db.commit()
    start_worker_once()
    return {"job": serialize_job(job)}


@router.get("/feeds/{feed_id}/episodes")
def feed_episodes(
    feed_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """What has been seen in this feed, newest first."""
    feed = db.get(Feed, feed_id)
    if not feed or feed.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Feed not found")
    rows = db.scalars(
        select(FeedEpisode)
        .where(FeedEpisode.feed_id == feed.id)
        .order_by(FeedEpisode.created_at.desc())
        .limit(50)
    ).all()
    return {
        "episodes": [
            {
                "id": row.id,
                "title": row.title,
                "published": row.published,
                "status": row.status,
                "media_id": row.media_id,
                "error": row.error,
            }
            for row in rows
        ]
    }


@router.post("/rss/preview")
def rss_preview(payload: RssPreviewRequest, _: Annotated[User, Depends(current_user)]) -> dict:
    try:
        parsed = parse_feed_url(payload.url)
    except RssFetchError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    entries = []
    for entry in parsed.entries[:10]:
        enclosure = next((link for link in entry.get("links", []) if link.get("rel") == "enclosure"), None)
        entries.append(
            {
                "title": entry.get("title", "Untitled episode"),
                "published": entry.get("published", ""),
                "audio_url": enclosure.get("href") if enclosure else None,
            }
        )
    return {"title": parsed.feed.get("title", "Podcast feed"), "entries": entries}


class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    project_id: str


def serialize_template(template: Template) -> dict:
    return {
        "id": template.id,
        "name": template.name,
        "aspect_ratio": template.aspect_ratio,
        "scene": json.loads(template.scene_json or "{}"),
        "created_at": template.created_at.isoformat(),
    }


@router.get("/templates")
def templates(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    saved = db.scalars(
        select(Template)
        .where(Template.owner_id == user.id)
        .order_by(Template.created_at.desc())
    ).all()
    return {"templates": [serialize_template(item) for item in saved]}


@router.post("/templates")
def create_template(
    payload: TemplateCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Save a project's look for reuse on later episodes."""
    project = db.get(Project, payload.project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    template = Template(
        owner_id=user.id,
        name=payload.name.strip(),
        aspect_ratio=project.aspect_ratio,
        scene_json=json.dumps(scene_for_template(json.loads(project.scene_json or "{}"))),
    )
    db.add(template)
    db.commit()
    return {"template": serialize_template(template)}


@router.delete("/templates/{template_id}")
def delete_template(
    template_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    template = db.get(Template, template_id)
    if not template or template.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Template not found")
    db.delete(template)
    db.commit()
    return {"ok": True}


@router.post("/projects/{project_id}/template/{template_id}")
def apply_template_to_project(
    project_id: str,
    template_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Lay a saved design over a project, keeping the episode's own media."""
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    template = db.get(Template, template_id)
    if not template or template.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Template not found")

    scene = apply_template(
        json.loads(project.scene_json or "{}"),
        json.loads(template.scene_json or "{}"),
    )
    # A design saved in one shape has to be remapped into this project's.
    if template.aspect_ratio != project.aspect_ratio:
        scene = remap_scene(scene, template.aspect_ratio, project.aspect_ratio)

    project.scene_json = json.dumps(scene)
    db.commit()
    return {"project": serialize_project(project)}


@router.get("/library/sounds")
def list_sounds(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(current_user)],
    kind: str | None = None,
    genre: str | None = None,
    search: str | None = None,
    limit: int = 200,
) -> dict:
    if kind not in (None, "music", "sfx"):
        raise HTTPException(status_code=400, detail="kind must be music or sfx")
    sounds = query_sounds(db, kind=kind, genre=genre, search=search, limit=max(1, min(limit, 500)))
    return {"sounds": [serialize_sound(sound) for sound in sounds]}


@router.get("/library/genres")
def list_genres(db: Annotated[Session, Depends(get_db)], _: Annotated[User, Depends(current_user)]) -> dict:
    return {"genres": library_genres(db)}


@router.get("/library/packs")
def list_packs(db: Annotated[Session, Depends(get_db)], _: Annotated[User, Depends(current_user)]) -> dict:
    return {"packs": installed_packs(db)}


@router.get("/library/sfx")
def sfx_roles(db: Annotated[Session, Depends(get_db)], _: Annotated[User, Depends(current_user)]) -> dict:
    """Role-to-URL map so the interface can play its own click and error cues."""
    sounds = query_sounds(db, kind="sfx", limit=100)
    by_stem = {Path(sound.relative_path).stem: sound for sound in sounds}
    roles = {
        role: f"/api/library/sounds/{by_stem[role].id}/file"
        for role in SFX_ROLES
        if role in by_stem
    }
    attribution = next((sound.attribution for sound in sounds), "")
    return {"roles": roles, "attribution": attribution}


@router.get("/library/sounds/{sound_id}/file")
def sound_file(sound_id: str, db: Annotated[Session, Depends(get_db)], _: Annotated[User, Depends(current_user)]):
    sound = db.get(SoundAsset, sound_id)
    if not sound:
        raise HTTPException(status_code=404, detail="Sound not found")
    path = sound_path(sound)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Sound file is missing from the library")
    media_type = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
        ".m4a": "audio/mp4",
        ".flac": "audio/flac",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.post("/library/sync")
def resync_library(db: Annotated[Session, Depends(get_db)], _: Annotated[User, Depends(current_admin)]) -> dict:
    return {"catalog": sync_catalog(db)}


class TranscriptionSettings(BaseModel):
    model: str | None = None
    language: str | None = None
    enabled: bool | None = None


@router.get("/settings/transcription")
def get_transcription_settings(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(current_user)],
) -> dict:
    """What the transcriber will do, and what this machine can actually run."""
    stored = {
        key: (db.get(AppSetting, key).value if db.get(AppSetting, key) else None)
        for key in ("whisper_model", "whisper_language", "transcribe_enabled")
    }
    model = stored["whisper_model"] or settings.whisper_model
    runtime = choose_transcription_runtime(model, prefer_gpu=settings.prefer_gpu)
    return {
        "model": runtime.model_size,
        "language": stored["whisper_language"] or settings.transcribe_language or "",
        "enabled": (
            settings.transcribe_enabled
            if stored["transcribe_enabled"] is None
            else stored["transcribe_enabled"] == "true"
        ),
        "installed": transcription_installed(),
        "device": runtime.device,
        "compute_type": runtime.compute_type,
        "models": list(WHISPER_MODEL_SIZES),
        "encoder": describe_encoder(),
    }


@router.put("/settings/transcription")
def set_transcription_settings(
    payload: TranscriptionSettings,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(current_admin)],
) -> dict:
    if payload.model is not None and payload.model not in WHISPER_MODEL_SIZES:
        raise HTTPException(status_code=400, detail=f"Unknown model: {payload.model}")

    updates = {
        "whisper_model": payload.model,
        # An empty language means "detect it", which is the default.
        "whisper_language": payload.language,
        "transcribe_enabled": None if payload.enabled is None else str(payload.enabled).lower(),
    }
    for key, value in updates.items():
        if value is None:
            continue
        setting = db.get(AppSetting, key) or AppSetting(key=key, value="")
        setting.value = value
        db.merge(setting)
    db.commit()
    return get_transcription_settings(db, _)


class VariantRequest(BaseModel):
    ratios: list[str] = Field(min_length=1, max_length=4)
    render: bool = True


@router.get("/settings/llm")
def llm_settings(_: Annotated[User, Depends(current_user)]) -> dict:
    """Whether clip suggestions are being read by a model, and why not."""
    return llm.runtime_status()


@router.get("/platforms")
def platform_specs() -> dict:
    """Every destination's requirements: shape, length, size, codecs."""
    return {"platforms": [platform_as_dict(p) for p in PLATFORMS]}


@router.get("/projects/{project_id}/destinations")
def project_destinations(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Where this clip can actually be posted, and what is stopping it.

    Checked against the rendered file when there is one, because file size is
    the constraint you cannot know in advance — and the one that fails at the
    upload step after the render is already paid for.
    """
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    rendered = settings.outputs_dir / project.id / "audiogram.mp4"
    file_bytes = rendered.stat().st_size if rendered.exists() else None
    duration = max(0.0, project.clip_end - project.clip_start)

    return {
        "duration": round(duration, 2),
        "aspect_ratio": project.aspect_ratio,
        "file_bytes": file_bytes,
        "rendered": file_bytes is not None,
        "destinations": check_platforms(project.aspect_ratio, duration, file_bytes),
    }


@router.get("/ratios")
def list_ratios(_: Annotated[User, Depends(current_user)]) -> dict:
    """The aspect ratios a project can be produced in, and what each is for."""
    return {
        "ratios": [
            {"ratio": ratio, **preset, "dimensions": RATIO_DIMENSIONS[ratio]}
            for ratio, preset in RATIO_PRESETS.items()
        ]
    }


@router.post("/projects/{project_id}/variants")
def create_variants(
    project_id: str,
    payload: VariantRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Copy a project into other aspect ratios, and optionally render them all.

    One clip usually needs to go to more than one place. Renders run in parallel
    lanes, so three variants finish in about the time one used to.
    """
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    wanted = list(dict.fromkeys(payload.ratios))  # de-duplicate, keep order
    unknown = [ratio for ratio in wanted if ratio not in RATIO_DIMENSIONS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown aspect ratio: {unknown[0]}")

    scene = json.loads(project.scene_json or "{}")
    created, jobs = [], []
    for ratio in wanted:
        if ratio == project.aspect_ratio:
            # Varying into the shape it already is would just be a copy.
            continue
        variant = Project(
            owner_id=user.id,
            media_id=project.media_id,
            title=variant_title(project.title, ratio),
            clip_start=project.clip_start,
            clip_end=project.clip_end,
            aspect_ratio=ratio,
            scene_json=json.dumps(remap_scene(scene, project.aspect_ratio, ratio)),
        )
        db.add(variant)
        db.commit()
        created.append(variant)

        if payload.render:
            job = Job(
                owner_id=user.id,
                kind=JobKind.render,
                subject_id=variant.id,
                message=f"Queued {ratio} render",
            )
            db.add(job)
            db.commit()
            jobs.append(job)

    if payload.render and jobs:
        start_worker_once()

    return {
        "projects": [serialize_project(item) for item in created],
        "jobs": [serialize_job(job) for job in jobs],
    }


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Stop a job, whether it is waiting or already running.

    A queued job is cancelled outright. A running one is asked to stop and its
    child process is killed, because marking the row alone would let FFmpeg
    finish and publish its output regardless.
    """
    job = db.get(Job, job_id)
    if not job or job.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status in {JobStatus.complete, JobStatus.failed, JobStatus.canceled}:
        # Finished work is not cancellable, and saying so beats pretending.
        raise HTTPException(
            status_code=409, detail=f"That job already {job.status.value}"
        )

    if job.status == JobStatus.queued:
        job.status = JobStatus.canceled
        job.message = "Cancelled before it started"
        db.commit()
    else:
        # The worker notices between steps, or when its child dies.
        cancellation.request(job.id)
        job.message = "Cancelling"
        db.commit()

    return {"job": serialize_job(job)}
