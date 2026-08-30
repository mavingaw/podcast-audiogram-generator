from __future__ import annotations

import io
import json
import uuid
import secrets
import shutil
import time
import zipfile
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter, Cookie, Depends, HTTPException, Request, Response, UploadFile,
)
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.deps import current_admin, current_user, optional_user
from app.core.config import settings
from app.db.models import (
    AppSetting, Feed, FeedEpisode, Job, JobKind, JobStatus, MediaAsset, Project, ShareLink,
    SoundAsset, Template, User,
)
from app.db.session import SessionLocal, get_db
from app.services.auth import create_session, delete_session, hash_password, verify_password
from app.services import cancellation
from app.services import facts as fact_service
from app.services import youtube as youtube_service
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
from app.services.batching import BatchError, make_clips, suggestions_for
from app.services.clipfinder import find as find_clips
from app.services.fingerprint import fingerprint as fingerprint_project
from app.services.snapping import snap as snap_clip_range
from app.services.platforms import PLATFORMS, as_dict as platform_as_dict
from app.services.platforms import check_all as check_platforms
from app.services.waveform import MAX_BUCKETS as MAX_PEAK_BUCKETS
from app.services.waveform import decode as decode_peaks
from app.services.waveform import duration_of as waveform_duration
from app.services.waveform import resample as resample_peaks
from app.services import chunked_upload, preview, revisions
from app.services.storage import UploadValidationError, contained_path, is_image, save_upload

router = APIRouter(prefix="/api")
# Public pages that live outside /api: the shared-clip page and its video.
public_router = APIRouter()


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


def serialize_media(media: MediaAsset, transcript: bool = True) -> dict:
    payload = {
        "has_waveform": bool(media.peaks_json),
        "id": media.id,
        "original_name": media.original_name,
        "content_type": media.content_type,
        "size_bytes": media.size_bytes,
        "duration_seconds": media.duration_seconds,
        "created_at": media.created_at.isoformat(),
        "has_transcript": bool(media.transcript_json),
        "artwork_media_id": media.artwork_media_id,
    }
    if transcript:
        payload["transcript"] = json.loads(media.transcript_json) if media.transcript_json else None
    return payload


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


@router.get("/settings/invite")
def invite_link(
    request: Request,
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """The sign-up code and a link that carries it, for the owner to share.

    The code lives in the container's environment, which the person who set
    it up can read and nobody else can; showing it to administrators in the
    app is what makes it usable — a link to paste into a message rather than
    a value to dig out of a template.
    """
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administrators only")
    code = signup_code_required()
    if not code:
        return {"code": None, "link": None}
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return {"code": code, "link": f"{proto}://{host}/?invite={code}"}


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


@router.get("/facts")
def random_facts(
    _: Annotated[User, Depends(current_user)],
    n: int = 8,
) -> dict:
    """Trivia for the progress bar. Served from a pool the box fills in the
    background; the browser never waits on the internet for these."""
    return {"facts": fact_service.pool.sample(max(1, min(30, n)))}


@router.get("/session")
def session_state(user: Annotated[User | None, Depends(optional_user)]) -> dict:
    """Who is signed in, if anyone — a 200 either way.

    The app asks this on every cold load. Asking `/me` instead meant every
    visit to the sign-in page began with a 401 in the browser console, which
    is noise for anyone debugging and a false alarm for the smoke test.
    """
    return {"user": serialize_user(user) if user else None}


@router.get("/users")
def list_users(db: Annotated[Session, Depends(get_db)], _: Annotated[User, Depends(current_admin)]) -> dict:
    users = db.scalars(select(User).order_by(User.created_at.asc())).all()
    return {"users": [serialize_user(user) for user in users]}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Remove an account and everything it owns.

    The way access is revoked. Files are removed with the rows: their
    uploads, their renders, their recordings. Not the library — that is the
    instance's, not theirs. An administrator cannot remove themselves, which
    is the only way an instance ends up with nobody who can administer it.
    """
    import shutil

    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administrators only")
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == user.id:
        raise HTTPException(status_code=400, detail="You cannot remove your own account")

    from app.db.models import Feed, FeedEpisode, ProjectRevision, Template

    projects = db.scalars(select(Project).where(Project.owner_id == target.id)).all()
    for project in projects:
        shutil.rmtree(settings.outputs_dir / project.id, ignore_errors=True)
        db.query(ProjectRevision).filter(ProjectRevision.project_id == project.id).delete()
        db.delete(project)
    for media in db.scalars(select(MediaAsset).where(MediaAsset.owner_id == target.id)).all():
        try:
            contained_path(settings.uploads_dir, settings.uploads_dir / media.stored_name).unlink(missing_ok=True)
        except (ValueError, OSError):
            pass
        db.delete(media)
    for feed in db.scalars(select(Feed).where(Feed.owner_id == target.id)).all():
        db.query(FeedEpisode).filter(FeedEpisode.feed_id == feed.id).delete()
        db.delete(feed)
    db.query(Template).filter(Template.owner_id == target.id).delete()
    db.query(Job).filter(Job.owner_id == target.id).delete()
    db.delete(target)
    db.commit()
    return {"ok": True, "removed_projects": len(projects)}


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
    return _register_upload(
        db, user,
        original_name=file.filename or "upload.bin",
        stored_name=stored_name,
        content_type=file.content_type or "application/octet-stream",
        size=size,
    )


def _register_upload(db, user, *, original_name: str, stored_name: str,
                     content_type: str, size: int) -> dict:
    """Put a saved file into the library and queue the work it needs.

    Shared by the single-request and chunked upload paths so that a file that
    arrived in pieces is indistinguishable afterwards from one that did not.
    """
    media = MediaAsset(
        owner_id=user.id,
        original_name=original_name,
        stored_name=stored_name,
        content_type=content_type,
        size_bytes=size,
    )
    if not is_image(original_name):
        # The show's artwork, if one has been chosen: an uploaded episode
        # then gets the same cover-art background a feed episode does.
        media.artwork_media_id = show_artwork_id(db, user)
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


def _artwork_key(user: User) -> str:
    return f"artwork:{user.id}"


def show_artwork_id(db: Session, user: User) -> str | None:
    """The image chosen as this person's show artwork, if it still exists."""
    setting = db.get(AppSetting, _artwork_key(user))
    if not setting or not setting.value:
        return None
    image = db.get(MediaAsset, setting.value)
    if not image or image.owner_id != user.id or not is_image(image.original_name):
        return None
    return image.id


@router.get("/settings/artwork")
def get_show_artwork(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    return {"media_id": show_artwork_id(db, user)}


class ShowArtwork(BaseModel):
    media_id: str | None = None


@router.put("/settings/artwork")
def set_show_artwork(
    payload: ShowArtwork,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Choose the show's artwork. Applied to every episode already uploaded
    that has none of its own, and to every upload after."""
    if payload.media_id:
        image = db.get(MediaAsset, payload.media_id)
        if not image or image.owner_id != user.id:
            raise HTTPException(status_code=404, detail="Image not found")
        if not is_image(image.original_name):
            raise HTTPException(status_code=400, detail="That file is not an image")
    key = _artwork_key(user)
    setting = db.get(AppSetting, key) or AppSetting(key=key, value="")
    setting.value = payload.media_id or ""
    db.merge(setting)
    backfilled = 0
    if payload.media_id:
        for media in db.scalars(select(MediaAsset).where(MediaAsset.owner_id == user.id)).all():
            if is_image(media.original_name) or media.artwork_media_id:
                continue
            media.artwork_media_id = payload.media_id
            backfilled += 1
    db.commit()
    return {"media_id": payload.media_id, "applied_to": backfilled}


@router.post("/media/upload/begin")
def begin_chunked_upload(
    payload: dict,
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Open a chunked upload.

    Anything over about 100 MB cannot be posted in one request from outside the
    LAN: Cloudflare rejects it at the edge before it reaches us. See
    services/chunked_upload.py.
    """
    try:
        session = chunked_upload.begin(
            owner_id=user.id,
            filename=str(payload.get("filename") or ""),
            content_type=str(payload.get("content_type") or ""),
            total_bytes=int(payload.get("total_bytes") or 0),
        )
    except UploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        # A total_bytes that is not a number, mostly.
        raise HTTPException(status_code=400, detail="Invalid upload request") from exc
    return {
        "upload_id": session.id,
        "chunk_bytes": chunked_upload.CHUNK_BYTES,
        "received": session.received,
    }


@router.put("/media/upload/{upload_id}/chunk/{index}")
async def append_chunked_upload(
    upload_id: str,
    index: int,
    request: Request,
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Append one slice, read from the raw body.

    Raw rather than multipart: the body is the chunk and nothing else, so there
    is no parser between the socket and the disk and no per-chunk filename to
    disagree with the one the upload was opened under.
    """
    body = await request.body()
    try:
        session = chunked_upload.append(upload_id, user.id, index, body)
    except UploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {
        "received": session.received,
        "total_bytes": session.total_bytes,
        "next_index": session.next_index,
    }


@router.post("/media/upload/{upload_id}/finish")
def finish_chunked_upload(
    upload_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    try:
        stored_name, size, session = chunked_upload.finish(upload_id, user.id)
    except UploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return _register_upload(
        db, user, original_name=session.filename,
        stored_name=stored_name, content_type=session.content_type, size=size,
    )


@router.delete("/media/upload/{upload_id}")
def abort_chunked_upload(
    upload_id: str,
    user: Annotated[User, Depends(current_user)],
) -> dict:
    chunked_upload.abort(upload_id, user.id)
    return {"ok": True}


@router.delete("/media/{media_id}")
def delete_media(
    media_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Remove a file from the library, and its bytes from the disk.

    Without this the library only ever grows: a mistaken upload, a duplicate
    from a retried upload, a test file, all permanent and all still occupying
    the disk. Projects that used it keep working — `media_id` is nullable and
    already set to NULL on delete — so removing a source does not silently
    destroy the clips someone made from it.
    """
    media = db.get(MediaAsset, media_id)
    if media is None or media.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Media not found")

    using = db.scalars(
        select(Project).where(Project.media_id == media.id)
    ).all()

    stored = media.stored_name
    db.delete(media)
    db.commit()

    # The row goes first: an orphaned row pointing at a missing file is a
    # broken library, while an orphaned file is only wasted space that the
    # next delete of the same name would clear anyway.
    try:
        path = contained_path(settings.uploads_dir, settings.uploads_dir / stored)
        path.unlink(missing_ok=True)
    except (ValueError, OSError):
        pass

    return {"ok": True, "projects_affected": len(using)}


@router.get("/media")
def list_media(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
    transcripts: bool = True,
) -> dict:
    """The library. `transcripts=0` leaves the word-level transcripts out.

    A transcript of an hour-long episode is most of a megabyte, and the app
    polls this list every couple of seconds while anything is running: with
    two transcribed episodes that was 1.5 MB a poll, per open tab, for a
    field nothing in the poll was reading. The poll asks for the light form
    and fetches one media's transcript when it actually needs it.
    """
    items = db.scalars(select(MediaAsset).where(MediaAsset.owner_id == user.id).order_by(MediaAsset.created_at.desc())).all()
    return {"media": [serialize_media(item, transcript=transcripts) for item in items]}


@router.get("/media/{media_id}")
def get_media(
    media_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """One media record with its transcript."""
    return {"media": serialize_media(_owned_media(db, media_id, user))}


@router.post("/media/{media_id}/transcribe")
def transcribe_media_again(
    media_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Queue transcription for a source that has none.

    A transcription can fail for reasons that have nothing to do with the
    audio — a model that could not be downloaded, a box restarted mid-job —
    and until now the only way past was to upload the file again. One job is
    queued; asking twice while it is still pending returns the pending one.
    """
    media = _owned_media(db, media_id, user)
    if is_image(media.original_name):
        raise HTTPException(status_code=400, detail="That file is an image; there is nothing to transcribe.")
    pending = db.scalar(
        select(Job).where(
            Job.subject_id == media.id,
            Job.kind == JobKind.transcribe,
            Job.status.in_((JobStatus.queued, JobStatus.running)),
        )
    )
    if pending is None:
        pending = Job(owner_id=user.id, kind=JobKind.transcribe, subject_id=media.id, message="Queued transcription")
        db.add(pending)
        db.commit()
        start_worker_once()
    return {"job": serialize_job(pending)}


@router.get("/media/{media_id}/file")
def media_file(media_id: str, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(current_user)]):
    media = db.get(MediaAsset, media_id)
    if not media or media.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Media not found")
    path = contained_path(settings.uploads_dir, settings.uploads_dir / media.stored_name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Media file not found")
    return FileResponse(path, media_type=media.content_type, filename=media.original_name)


@router.post("/projects/{project_id}/voiceover")
async def save_voiceover(
    project_id: str,
    file: UploadFile,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Keep a recording made in Studio, so it can be placed on the clip.

    Stored as an ordinary media asset — the same table, the same delete —
    but without the analysis, waveform and transcription jobs an episode
    gets: a ten-second aside does not need a transcript. The browser hands
    over whatever MediaRecorder produced (webm/opus in Chrome, mp4 in
    Safari); FFmpeg reads either at render time.
    """
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    kind = (file.content_type or "").split(";")[0].strip().lower()
    suffix = {"audio/webm": ".webm", "video/webm": ".webm", "audio/mp4": ".m4a",
              "audio/ogg": ".ogg", "audio/wav": ".wav", "audio/x-wav": ".wav"}.get(kind)
    if suffix is None:
        raise HTTPException(status_code=415, detail="That recording format is not supported")
    stored = f"{uuid.uuid4()}{suffix}"
    target = contained_path(settings.uploads_dir, settings.uploads_dir / stored)
    size = 0
    with target.open("wb") as handle:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > 64 * 1024 * 1024:
                target.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="That recording is too large")
            handle.write(chunk)
    if size == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The recording was empty")
    media = MediaAsset(
        owner_id=user.id,
        original_name=f"Voice-over for {project.title[:80]}{suffix}",
        stored_name=stored,
        content_type=kind,
        size_bytes=size,
    )
    db.add(media)
    db.commit()
    # Its words belong in the captions. Transcription only: a ten-second
    # aside needs no waveform or analysis, and the job is quick.
    db.add(Job(owner_id=user.id, kind=JobKind.transcribe, subject_id=media.id,
               message="Queued voice-over transcription"))
    db.commit()
    start_worker_once()
    return {"media": serialize_media(media)}


@router.get("/projects/{project_id}/preview.m4a")
def project_preview_audio(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
):
    """The clip's own audio, and nothing else, for the Studio player.

    Studio used to play the full episode file and seek into it. From outside
    the LAN that meant pulling a 90 MB MP3 through the tunnel before the
    first second of preview would play, and the scrubber ran over ninety
    minutes of audio the clip did not contain. This is the clip cut out
    server-side — a 45-second clip is under a megabyte — so Studio opens at
    once and its timeline is the clip's, 0 to its length.

    Cached per (media, start, end) under the work directory, so moving the
    clip edges produces a new file and every reopen of the same clip is a
    file read. Not the render: no cuts, no music, no loudness pass. Those
    are what the export is for; this is what you scrub.
    """
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id or not project.media_id:
        raise HTTPException(status_code=404, detail="Project not found")
    media = db.get(MediaAsset, project.media_id)
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")
    source = contained_path(settings.uploads_dir, settings.uploads_dir / media.stored_name)
    if not source.exists():
        raise HTTPException(status_code=404, detail="Media file not found")
    try:
        target = preview.ensure(source, media.id, project.clip_start, project.clip_end)
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    media_type = "audio/mpeg" if target.suffix == ".mp3" else "audio/mp4"
    return FileResponse(target, media_type=media_type, headers={"Cache-Control": "private, max-age=86400"})


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


def _soundbites_for(db, media: MediaAsset) -> list[dict]:
    """Any moments the podcaster marked, if this media came from a feed.

    Read for the manual suggestion path too, not only the automatic one: a
    soundbite is the podcaster's own choice of the best passage, and hiding it
    from the person who pressed the button would be perverse.
    """
    from app.db.models import FeedEpisode

    episode = db.scalar(select(FeedEpisode).where(FeedEpisode.media_id == media.id))
    if not episode or not episode.soundbites_json:
        return []
    try:
        return json.loads(episode.soundbites_json)
    except ValueError:
        return []


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
    # Or a starter look (the Quick Create tiles) as plain scene fields.
    look: dict | None = None


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
            soundbites=_soundbites_for(db, media),
            artwork_media_id=media.artwork_media_id,
            aspect_ratio=payload.aspect_ratio,
            template_id=payload.template_id,
            render=payload.render,
            look=payload.look,
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

    # The same function the batch button and the feed watcher use, so a clip
    # suggested here is the clip those would have made. It puts anything the
    # podcaster marked with <podcast:soundbite> first.
    clips = suggestions_for(
        media, transcript, limit, soundbites=_soundbites_for(db, media)
    )
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

    # Keep how it was before applying the change. Throttled, so dragging a
    # slider produces one entry rather than one per frame; see
    # services/revisions.py.
    revisions.record(db, project, updates)

    if "scene" in updates:
        project.scene_json = json.dumps(updates.pop("scene"))
    for key, value in updates.items():
        setattr(project, key, value)
    db.commit()
    if ("clip_start" in updates or "clip_end" in updates) and project.media_id:
        # Cut the Studio preview now, in the background, so opening the clip
        # a moment later finds it already there.
        media = db.get(MediaAsset, project.media_id)
        if media:
            source = settings.uploads_dir / media.stored_name
            if source.exists():
                preview.warm(source, media.id, project.clip_start, project.clip_end)
    return {"project": serialize_project(project)}


@router.get("/projects/{project_id}/revisions")
def list_revisions(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """The project's recent past, newest first."""
    from app.db.models import ProjectRevision

    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    rows = db.scalars(
        select(ProjectRevision)
        .where(ProjectRevision.project_id == project.id)
        .order_by(ProjectRevision.created_at.desc())
    ).all()
    return {"revisions": [
        {
            "id": row.id,
            "label": row.label,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]}


@router.post("/projects/{project_id}/revisions/{revision_id}/restore")
def restore_revision(
    project_id: str,
    revision_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Put the project back to a recorded state.

    The current state is recorded first, so reaching for history and landing
    somewhere worse is not a one-way door.
    """
    from app.db.models import ProjectRevision

    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    revision = db.get(ProjectRevision, revision_id)
    if not revision or revision.project_id != project.id:
        raise HTTPException(status_code=404, detail="Revision not found")

    revisions.restore(db, project, revision)
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
    allowed = {"audiogram.mp4", "captions.srt", "captions.vtt", "render-manifest.json", "CREDITS.txt", "poster.jpg"}
    if filename not in allowed:
        raise HTTPException(status_code=404, detail="Output not found")
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    output_path = contained_path(settings.outputs_dir, settings.outputs_dir / project.id / filename)
    if filename == "poster.jpg" and not output_path.exists():
        # Renders made before posters existed: cut the frame now, once.
        from app.services.jobs import _write_poster

        _write_poster(settings.outputs_dir / project.id)
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="Output not found")
    media_type = {
        ".jpg": "image/jpeg",
        ".mp4": "video/mp4",
        ".srt": "application/x-subrip",
        ".vtt": "text/vtt",
        ".json": "application/json",
        ".txt": "text/plain; charset=utf-8",
    }.get(output_path.suffix, "application/octet-stream")
    return FileResponse(output_path, media_type=media_type, filename=filename)


@router.post("/projects/{project_id}/share")
def share_project(
    project_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """A link anyone can open to watch and download this project's video.

    One live link per project: asking again returns the same one, so a link
    already sent keeps working. Revoking makes a fresh token next time.
    """
    import secrets

    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    video = settings.outputs_dir / project.id / "audiogram.mp4"
    if not video.exists():
        raise HTTPException(status_code=409, detail="Export the clip first, then share it.")
    link = db.scalar(
        select(ShareLink).where(ShareLink.project_id == project.id, ShareLink.revoked.is_(False))
    )
    if link is None:
        link = ShareLink(token=secrets.token_urlsafe(24), project_id=project.id, owner_id=user.id)
        db.add(link)
        db.commit()
    return {"token": link.token, "url": str(request.base_url).rstrip("/") + f"/s/{link.token}"}


@router.delete("/projects/{project_id}/share")
def unshare_project(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    count = 0
    for link in db.scalars(select(ShareLink).where(ShareLink.project_id == project.id, ShareLink.revoked.is_(False))).all():
        link.revoked = True
        count += 1
    db.commit()
    return {"revoked": count}


def _shared_project(db: Session, token: str) -> Project:
    link = db.get(ShareLink, token)
    if not link or link.revoked:
        raise HTTPException(status_code=404, detail="This link is no longer available")
    project = db.get(Project, link.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="This link is no longer available")
    return project


@public_router.get("/s/{token}/video.mp4")
def shared_video(token: str, db: Annotated[Session, Depends(get_db)]):
    project = _shared_project(db, token)
    video = settings.outputs_dir / project.id / "audiogram.mp4"
    if not video.exists():
        raise HTTPException(status_code=404, detail="This link is no longer available")
    return FileResponse(video, media_type="video/mp4", filename=_share_filename(project))


def _share_filename(project: Project) -> str:
    safe = "".join(ch if ch.isalnum() or ch in " -_" else "" for ch in project.title).strip()[:60]
    return (safe or "clip") + ".mp4"


@public_router.get("/s/{token}/poster.jpg")
def shared_poster(token: str, db: Annotated[Session, Depends(get_db)]):
    project = _shared_project(db, token)
    from app.services.jobs import _write_poster

    poster = settings.outputs_dir / project.id / "poster.jpg"
    if not poster.exists():
        _write_poster(settings.outputs_dir / project.id)
    if not poster.exists():
        raise HTTPException(status_code=404, detail="No preview image")
    return FileResponse(poster, media_type="image/jpeg")


@public_router.get("/s/{token}", response_class=HTMLResponse)
def shared_page(token: str, db: Annotated[Session, Depends(get_db)]) -> str:
    """A plain page: the video, a download button, nothing to sign in to."""
    import html

    project = _shared_project(db, token)
    title = html.escape(project.title)
    ratio = project.aspect_ratio.replace(":", " / ")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta property="og:title" content="{title}"><meta property="og:type" content="video.other">
<meta property="og:video" content="/s/{token}/video.mp4"><meta property="og:video:type" content="video/mp4">
<meta property="og:image" content="/s/{token}/poster.jpg">
<style>
  body{{margin:0;background:#0B0D11;color:#F8FAFC;font:15px/1.5 -apple-system,Segoe UI,Inter,sans-serif;display:grid;place-items:center;min-height:100vh;padding:24px;box-sizing:border-box}}
  main{{display:grid;gap:16px;width:min(480px,100%);text-align:center}}
  video{{width:100%;aspect-ratio:{ratio};background:#000;border-radius:14px;box-shadow:0 20px 60px rgba(0,0,0,.6)}}
  h1{{font-size:18px;margin:0;font-weight:700}}
  .button{{display:inline-block;padding:12px 20px;border-radius:10px;background:#89CFF0;color:#0B0D11;font-weight:700;text-decoration:none;border:0;font:inherit;font-weight:700;cursor:pointer}}
  .button.quiet{{background:#1f2937;color:#F8FAFC}}
  .row{{display:flex;gap:10px;justify-content:center;flex-wrap:wrap}}
  small{{color:#94A3B8}}
</style></head><body><main>
<h1>{title}</h1>
<video src="/s/{token}/video.mp4" poster="/s/{token}/poster.jpg" controls playsinline preload="metadata"></video>
<div class="row"><a class="button" href="/s/{token}/video.mp4" download="{html.escape(_share_filename(project))}">Download video</a>
<button class="button quiet" id="post" hidden>Post to…</button></div>
<small id="hint">Made with Kinder</small>
<script>
(function() {{
  // On a phone the share sheet lists Instagram, TikTok, YouTube and the
  // rest; the video goes straight into the app. Desktop browsers cannot
  // share files, so the button stays hidden and Download is the way.
  var btn = document.getElementById('post');
  if (!navigator.share || !navigator.canShare) return;
  btn.hidden = false;
  btn.onclick = async function() {{
    btn.disabled = true; btn.textContent = 'Getting the video…';
    try {{
      var blob = await (await fetch('/s/{token}/video.mp4')).blob();
      var file = new File([blob], {html.escape(json.dumps(_share_filename(project)))}, {{type: 'video/mp4'}});
      if (navigator.canShare({{files: [file]}})) await navigator.share({{files: [file], title: {html.escape(json.dumps(project.title))}}});
      else await navigator.share({{title: {html.escape(json.dumps(project.title))}, url: location.href}});
    }} catch (e) {{ /* cancelled */ }}
    btn.disabled = false; btn.textContent = 'Post to…';
  }};
}})();
</script>
</main></body></html>"""


# ---------------------------------------------------------------- YouTube


class YouTubeClient(BaseModel):
    client_id: str = ""
    client_secret: str = ""


@router.get("/settings/youtube")
def get_youtube_settings(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(current_admin)],
) -> dict:
    cid, secret = youtube_service.client(db)
    return {"client_id": cid, "has_secret": bool(secret)}


@router.put("/settings/youtube")
def set_youtube_settings(
    payload: YouTubeClient,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(current_admin)],
) -> dict:
    youtube_service.set_client(db, payload.client_id, payload.client_secret)
    cid, secret = youtube_service.client(db)
    return {"client_id": cid, "has_secret": bool(secret)}


def _youtube_redirect(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/api/youtube/callback"


@router.get("/youtube/account")
def youtube_account(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    acct = youtube_service.account(db, user.id)
    return {
        "configured": youtube_service.configured(db),
        "connected": bool(acct),
        "channel": (acct or {}).get("channel", ""),
    }


@router.get("/youtube/connect")
def youtube_connect(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """The Google sign-in URL. The browser goes there, approves, and comes
    back to the callback below with a code."""
    try:
        return {"url": youtube_service.begin(db, user.id, _youtube_redirect(request))}
    except youtube_service.YouTubeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/youtube/callback")
def youtube_callback(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
    code: str = "",
    state: str = "",
    error: str = "",
):
    if error or not code:
        return RedirectResponse(url="/?youtube=denied", status_code=303)
    try:
        youtube_service.finish(db, user.id, _youtube_redirect(request), code, state)
    except youtube_service.YouTubeError as failure:
        return RedirectResponse(url="/?youtube=failed&why=" + requests_quote(str(failure)), status_code=303)
    return RedirectResponse(url="/?youtube=connected", status_code=303)


def requests_quote(text: str) -> str:
    from urllib.parse import quote

    return quote(text[:200])


@router.delete("/youtube/account")
def youtube_disconnect(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    youtube_service.disconnect(db, user.id)
    return {"connected": False}


class YouTubePost(BaseModel):
    title: str = ""
    description: str = ""
    privacy: str = "private"


@router.post("/projects/{project_id}/post/youtube")
def post_to_youtube(
    project_id: str,
    payload: YouTubePost,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Upload this project's rendered video to the connected channel."""
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    video = settings.outputs_dir / project.id / "audiogram.mp4"
    try:
        result = youtube_service.upload(
            db, user.id, video,
            title=payload.title.strip() or project.title,
            description=payload.description,
            privacy=payload.privacy,
        )
    except youtube_service.YouTubeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    # Remembered on the project, so the card can show "Posted" next time.
    scene = json.loads(project.scene_json or "{}")
    posts = list(scene.get("posted") or [])
    posts.append({"platform": "youtube", "url": result["url"], "privacy": result["privacy"]})
    scene["posted"] = posts
    project.scene_json = json.dumps(scene)
    db.commit()
    return result


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
    # The docstring was a lie until this: the job applied the polling interval
    # regardless, so pressing "check now" within fifteen minutes of the last
    # check did nothing at all, silently. Clearing the timestamp is what makes
    # the feed due.
    for feed in db.scalars(
        select(Feed).where(Feed.owner_id == user.id, Feed.active.is_(True))
    ).all():
        feed.last_checked = None
    job = Job(owner_id=user.id, kind=JobKind.check_feeds, message="Checking feeds")
    db.add(job)
    db.commit()
    start_worker_once()
    return {"job": serialize_job(job)}


class ImportOlderRequest(BaseModel):
    count: int = Field(default=5, ge=1, le=50)


@router.post("/feeds/{feed_id}/import")
def import_older_episodes(
    feed_id: str,
    payload: ImportOlderRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> dict:
    """Pull the next N newest episodes that have not been imported.

    A feed's first sight takes only the newest episode on purpose — a show
    with 400 back-episodes must not enqueue 400 transcriptions by accident.
    This is the deliberate version: read the feed now, skip what is already
    known, and queue the next few, newest first. Capped at 50 a press.
    """
    from app.services import feeds as feedservice

    feed = db.get(Feed, feed_id)
    if not feed or feed.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Feed not found")
    try:
        parsed, _etag, _modified, _changed, raw = feedservice.fetch(feed.url, None, None)
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Could not read the feed: {error}") from error

    known = {
        row.guid for row in db.scalars(
            select(FeedEpisode).where(FeedEpisode.feed_id == feed.id)
        ).all()
    }
    queued = []
    for item in feedservice.episodes_of(parsed, raw):
        if item.guid in known:
            continue
        record = FeedEpisode(
            feed_id=feed.id, guid=item.guid, title=item.title,
            published=item.published, enclosure_url=item.url,
            soundbites_json=json.dumps([
                {"start": b.start, "duration": b.duration, "title": b.title}
                for b in item.soundbites
            ]) if item.soundbites else None,
            artwork_url=item.image_url,
        )
        db.add(record)
        db.flush()
        db.add(Job(
            owner_id=user.id, kind=JobKind.import_episode,
            subject_id=record.id, message=f"Queued {item.title[:60]}",
        ))
        queued.append({"id": record.id, "title": item.title})
        if len(queued) >= payload.count:
            break
    db.commit()
    if queued:
        start_worker_once()
    return {"queued": queued, "remaining": max(0, sum(
        1 for item in feedservice.episodes_of(parsed, raw) if item.guid not in known
    ) - len(queued))}


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
        scene_json=json.dumps(scene_for_template(
            json.loads(project.scene_json or "{}"),
            clip_seconds=max(0.5, float(project.clip_end) - float(project.clip_start)),
        )),
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
        clip_seconds=max(0.5, float(project.clip_end) - float(project.clip_start)),
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
