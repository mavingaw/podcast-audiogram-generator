from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import SessionToken, User

password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_lifetime() -> timedelta:
    return timedelta(days=max(1, settings.session_days))


def is_expired(session: SessionToken, now: datetime | None = None) -> bool:
    """Whether a token is past its life.

    Rows written before this check existed have a naive `created_at`; treat
    those as UTC rather than crashing on a comparison.
    """
    created = session.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - created > session_lifetime()


def purge_expired_sessions(db: Session) -> int:
    """Delete tokens that are past their life.

    Sessions used to be immortal: nothing ever removed a row, so a token issued
    once stayed valid forever and the table only grew. This runs opportunistically
    at sign-in, which is often enough to keep it bounded without a scheduler.
    """
    cutoff = datetime.now(timezone.utc) - session_lifetime()
    removed = db.execute(delete(SessionToken).where(SessionToken.created_at < cutoff))
    if removed.rowcount:
        db.commit()
    return removed.rowcount or 0


def create_session(db: Session, user: User) -> str:
    purge_expired_sessions(db)
    raw = secrets.token_urlsafe(48)
    db.add(SessionToken(token_hash=hash_token(raw), user_id=user.id))
    db.commit()
    return raw


def get_user_by_token(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    stmt = select(SessionToken).where(SessionToken.token_hash == hash_token(token))
    session = db.scalar(stmt)
    if not session:
        return None
    if is_expired(session):
        # Drop it on sight so a stale cookie cannot be replayed.
        db.delete(session)
        db.commit()
        return None
    if session.user.disabled:
        return None
    return session.user


def delete_session(db: Session, token: str | None) -> None:
    if not token:
        return
    session = db.scalar(select(SessionToken).where(SessionToken.token_hash == hash_token(token)))
    if session:
        db.delete(session)
        db.commit()

