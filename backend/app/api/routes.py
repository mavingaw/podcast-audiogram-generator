from __future__ import annotations

import json
import time
from typing import Annotated

import feedparser
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_admin, current_user
from app.core.config import settings
from app.db.models import AppSetting, Job, JobKind, MediaAsset, Project, User
from app.db.session import SessionLocal, get_db
from app.services.auth import create_session, delete_session, hash_password, verify_password
from app.services.gpu import discover_gpus
from app.services.jobs import start_worker_once
from app.services.storage import contained_path, save_upload

router = APIRouter(prefix="/api")


class BootstrapRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10)
    is_admin: bool = False


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=10)
    is_admin: bool | None = None
    disabled: bool | None = None


class ProjectCreate(BaseModel):
    media_id: str | None = None
    title: str


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


def serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "is_admin": user.is_admin,
        "disabled": user.disabled,
        "created_at": user.created_at.isoformat(),
    }


def serialize_media(media: MediaAsset) -> dict:
    return {
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
    user = User(email=payload.email.lower(), password_hash=hash_password(payload.password), is_admin=True)
    db.add(user)
    db.commit()
    token = create_session(db, user)
    response.set_cookie(settings.session_cookie, token, httponly=True, samesite="lax")
    return {"user": serialize_user(user)}


@router.post("/auth/login")
def login(payload: LoginRequest, response: Response, db: Annotated[Session, Depends(get_db)]) -> dict:
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or user.disabled or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_session(db, user)
    response.set_cookie(settings.session_cookie, token, httponly=True, samesite="lax")
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
    email = payload.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="A user with that email already exists")
    user = User(email=email, password_hash=hash_password(payload.password), is_admin=payload.is_admin)
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
    stored_name, size = await save_upload(file)
    media = MediaAsset(
        owner_id=user.id,
        original_name=file.filename or "upload.bin",
        stored_name=stored_name,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=size,
    )
    db.add(media)
    db.commit()
    analyze = Job(owner_id=user.id, kind=JobKind.analyze_media, subject_id=media.id, message="Queued media analysis")
    transcribe = Job(owner_id=user.id, kind=JobKind.transcribe, subject_id=media.id, message="Queued transcription")
    db.add_all([analyze, transcribe])
    db.commit()
    start_worker_once()
    return {"media": serialize_media(media), "jobs": [serialize_job(analyze), serialize_job(transcribe)]}


@router.get("/media")
def list_media(db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(current_user)]) -> dict:
    items = db.scalars(select(MediaAsset).where(MediaAsset.owner_id == user.id).order_by(MediaAsset.created_at.desc())).all()
    return {"media": [serialize_media(item) for item in items]}


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


@router.post("/projects/{project_id}/render")
def render_project(project_id: str, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(current_user)]) -> dict:
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    job = Job(owner_id=user.id, kind=JobKind.render, subject_id=project.id, message="Queued render")
    db.add(job)
    db.commit()
    start_worker_once()
    return {"job": serialize_job(job)}


@router.get("/projects/{project_id}/outputs/{filename}")
def download_project_output(
    project_id: str,
    filename: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
):
    allowed = {"audiogram.mp4", "captions.srt", "captions.vtt", "render-manifest.json"}
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


@router.get("/jobs/{job_id}/events")
def job_events(job_id: str, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(current_user)]):
    job = db.get(Job, job_id)
    if not job or job.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")

    def stream():
        while True:
            with SessionLocal() as scoped:
                current = scoped.get(Job, job_id)
                if not current:
                    break
                yield f"data: {json.dumps(serialize_job(current))}\n\n"
                if current.status.value in {"complete", "failed", "canceled"}:
                    break
            time.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/rss/preview")
def rss_preview(payload: RssPreviewRequest, _: Annotated[User, Depends(current_user)]) -> dict:
    if not payload.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Only HTTP(S) feeds are supported")
    parsed = feedparser.parse(payload.url)
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


@router.get("/templates")
def templates(_: Annotated[User, Depends(current_user)]) -> dict:
    return {
        "templates": [
            {
                "id": "clean-wave",
                "name": "Clean Wave",
                "aspect_ratios": ["9:16", "1:1", "16:9"],
                "scene": {"background": "#101820", "accent": "#23a094", "captionStyle": "clean"},
            },
            {
                "id": "editorial",
                "name": "Editorial",
                "aspect_ratios": ["9:16", "1:1"],
                "scene": {"background": "#f5f0e8", "accent": "#9a3b3b", "captionStyle": "serif"},
            },
        ]
    }
