from __future__ import annotations

import argparse
import getpass

from sqlalchemy import select

from app.db.init_db import init_db
from app.db.models import User
from app.db.session import SessionLocal
from app.services.auth import hash_password


def create_admin(email: str, password: str) -> None:
    init_db()
    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.email == email.lower()))
        if existing:
            existing.is_admin = True
            existing.disabled = False
            existing.password_hash = hash_password(password)
            db.commit()
            print(f"Updated admin user: {email.lower()}")
            return
        user = User(
            email=email.lower(),
            password_hash=hash_password(password),
            is_admin=True,
            disabled=False,
        )
        db.add(user)
        db.commit()
        print(f"Created admin user: {email.lower()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Podcast Audiogram Studio database setup")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Create or update database tables")

    admin_parser = subparsers.add_parser("create-admin", help="Create or reset an admin user")
    admin_parser.add_argument("--email", required=True)
    admin_parser.add_argument("--password")

    args = parser.parse_args()
    if args.command == "init":
        init_db()
        print("Database initialized")
        return
    if args.command == "create-admin":
        password = args.password or getpass.getpass("Password: ")
        if len(password) < 10:
            raise SystemExit("Password must be at least 10 characters")
        create_admin(args.email, password)


if __name__ == "__main__":
    main()

