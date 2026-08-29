from __future__ import annotations

import argparse
import getpass
import re

from sqlalchemy import select

from app.db.init_db import init_db
from app.db.models import User
from app.db.session import SessionLocal
from app.services.auth import hash_password

# Kept in step with USERNAME_PATTERN in app/api/routes.py.
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
MIN_PASSWORD_LENGTH = 10


def create_admin(username: str, password: str) -> None:
    """Create an administrator, or reset an existing one's password."""
    name = username.strip().lower()
    if not USERNAME_PATTERN.match(name):
        raise SystemExit(
            "Username must be 3-32 characters: letters, digits, dot, dash, or "
            "underscore, starting with a letter or digit."
        )

    init_db()
    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.username == name))
        if existing:
            existing.is_admin = True
            existing.disabled = False
            existing.password_hash = hash_password(password)
            db.commit()
            print(f"Updated admin user: {name}")
            return
        user = User(
            username=name,
            password_hash=hash_password(password),
            is_admin=True,
            disabled=False,
        )
        db.add(user)
        db.commit()
        print(f"Created admin user: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Kinder database setup")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Create or update database tables")

    admin_parser = subparsers.add_parser("create-admin", help="Create or reset an admin user")
    admin_parser.add_argument("--username", required=True)
    # Optional so it can be typed at a prompt rather than left in shell history.
    admin_parser.add_argument("--password")

    args = parser.parse_args()
    if args.command == "init":
        init_db()
        print("Database initialized")
        return
    if args.command == "create-admin":
        password = args.password or getpass.getpass("Password: ")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise SystemExit(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
        create_admin(args.username, password)


if __name__ == "__main__":
    main()
