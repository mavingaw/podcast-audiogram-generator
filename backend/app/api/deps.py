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

