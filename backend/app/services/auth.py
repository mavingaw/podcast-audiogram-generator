from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

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


def create_session(db: Session, user: User) -> str:
    raw = secrets.token_urlsafe(48)
    db.add(SessionToken(token_hash=hash_token(raw), user_id=user.id))
    db.commit()
    return raw


def get_user_by_token(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    stmt = select(SessionToken).where(SessionToken.token_hash == hash_token(token))
    session = db.scalar(stmt)
    if not session or session.user.disabled:
        return None
    return session.user


def delete_session(db: Session, token: str | None) -> None:
    if not token:
        return
    session = db.scalar(select(SessionToken).where(SessionToken.token_hash == hash_token(token)))
    if session:
        db.delete(session)
        db.commit()

