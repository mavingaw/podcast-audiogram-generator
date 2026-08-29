from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from app.db import models  # noqa: F401
from app.db.session import Base, engine

log = logging.getLogger(__name__)


def init_db() -> None:
    # The rename runs first: create_all would otherwise add a second, empty
    # username column alongside the email one it does not recognise.
    rename_email_to_username()
    Base.metadata.create_all(bind=engine)
    add_missing_columns()
    backfill_defaults()


def add_missing_columns() -> None:
    """Add columns the models declare but an existing database lacks.

    ``create_all`` creates missing *tables* and silently ignores missing
    *columns*, so a schema addition would otherwise break every installation
    that already has data. This handles the additive case — the only kind this
    project has needed — and leaves renames, drops, and type changes to a real
    migration tool if one ever becomes necessary.
    """
    with engine.begin() as connection:
        # Reflect through the connection doing the work, not through the engine:
        # an Inspector caches what it reads, so an engine-level one built before
        # the first ALTER reports stale columns and the next call re-adds them.
        inspector = inspect(connection)
        existing_tables = set(inspector.get_table_names())

        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                if not column.nullable and column.default is None and column.server_default is None:
                    log.warning(
                        "Cannot add required column %s.%s automatically; a migration is needed.",
                        table.name,
                        column.name,
                    )
                    continue
                column_type = column.type.compile(engine.dialect)
                connection.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}')
                )
                # `backfill_defaults` fills it in: SQLite puts NULL in a new
                # column, not the model's default.
                log.info("Added column %s.%s", table.name, column.name)


def backfill_defaults() -> None:
    """Fill NULLs left in columns that have a default.

    SQLite fills a newly added column with NULL: the model's default is applied
    by the ORM when a row is *created*, and these rows already existed. Every
    old row then reads back None, which is neither the previous behaviour nor
    the new one — `review_state` was added and sixteen existing projects came
    back with no state at all.

    Run on every start rather than only when a column is added, because the
    column may have been added by a previous version that did not backfill. It
    is idempotent and touches nothing once the rows are correct.
    """
    with engine.begin() as connection:
        inspector = inspect(connection)
        existing_tables = set(inspector.get_table_names())

        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name not in present or column.primary_key:
                    continue
                default = getattr(column.default, "arg", None)
                # Callables are per-row (timestamps, generated ids) and are not
                # something to stamp a whole table with.
                if default is None or callable(default):
                    continue
                result = connection.execute(
                    text(
                        f'UPDATE "{table.name}" SET "{column.name}" = :value '
                        f'WHERE "{column.name}" IS NULL'
                    ),
                    {"value": default},
                )
                if result.rowcount:
                    log.info(
                        "Backfilled %s row(s) of %s.%s",
                        result.rowcount, table.name, column.name,
                    )


def rename_email_to_username() -> None:
    """One-time move from email identity to username.

    ``add_missing_columns`` only adds; a rename needs its own step. Existing
    rows keep working: an address becomes its local part, so `mujin@example.com`
    signs in as `mujin`, and a collision gets a numeric suffix rather than
    failing the unique index and taking the whole startup down with it.
    """
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "username" in columns or "email" not in columns:
        return

    with engine.begin() as connection:
        connection.execute(text('ALTER TABLE "users" RENAME COLUMN "email" TO "username"'))
        rows = connection.execute(text('SELECT id, username FROM users')).fetchall()
        taken: set[str] = set()
        for user_id, value in rows:
            name = (value or "").split("@", 1)[0].strip().lower() or "user"
            candidate, suffix = name, 2
            while candidate in taken:
                candidate, suffix = f"{name}{suffix}", suffix + 1
            taken.add(candidate)
            if candidate != value:
                connection.execute(
                    text("UPDATE users SET username = :name WHERE id = :id"),
                    {"name": candidate, "id": user_id},
                )
        log.info("Renamed users.email to users.username for %d account(s)", len(rows))
