from __future__ import annotations

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import User
from app.db.session import get_db
from app.services.auth import get_user_by_token


def current_user(
    db: Session = Depends(get_db),
    pas_session: str | None = Cookie(default=None, alias=settings.session_cookie),
) -> User:
    user = get_user_by_token(db, pas_session)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user


def optional_user(
    db: Session = Depends(get_db),
    pas_session: str | None = Cookie(default=None, alias=settings.session_cookie),
) -> User | None:
    """The signed-in user, or None — for the one call that asks rather than requires."""
    return get_user_by_token(db, pas_session)


def current_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required")
    return user

