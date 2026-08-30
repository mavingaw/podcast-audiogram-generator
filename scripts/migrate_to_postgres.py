"""Move Kinder's data from SQLite to Postgres, table by table.

Run from the backend directory (or inside the container, where the app code
lives at /app):

    python scripts/migrate_to_postgres.py \
        --source sqlite:////config/app.db \
        --target postgresql+psycopg2://kinder:PASSWORD@HOST:5432/kinder

The target schema is created by the app's own models, the rows are copied in
foreign-key order, and the copy is verified by row counts before anything is
declared done. The SQLite file is never written to — if anything goes wrong,
pointing DATABASE_URL back at it is the whole rollback.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine, func, insert, select


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="sqlite:///... URL of the current database")
    parser.add_argument("--target", required=True, help="postgresql+psycopg2://... URL of the new one")
    parser.add_argument("--force", action="store_true", help="copy even if the target already has rows")
    args = parser.parse_args()

    from app.db import models  # noqa: F401 - registers every table
    from app.db.session import Base

    source = create_engine(args.source, future=True)
    target = create_engine(args.target, future=True)

    Base.metadata.create_all(bind=target)

    with source.connect() as src, target.begin() as dst:
        # Refuse to double-copy: a second run would violate primary keys and
        # a --force on a used database is somebody's explicit decision.
        if not args.force:
            for table in Base.metadata.sorted_tables:
                existing = dst.execute(select(func.count()).select_from(table)).scalar() or 0
                if existing:
                    print(f"Target already has {existing} row(s) in {table.name}; "
                          "use --force only if you mean to append.")
                    return 2

        total = 0
        copied: dict[str, int] = {}
        seen: dict[str, set] = {}  # table name -> primary-key values that landed
        for table in Base.metadata.sorted_tables:
            rows = [dict(row._mapping) for row in src.execute(select(table))]

            # SQLite tolerated orphans (rows pointing at parents that were
            # deleted, e.g. sessions of a removed account); Postgres will
            # not. Parents copy first (sorted_tables), so anything pointing
            # at a parent that did not land is garbage — skip and say so.
            checks = []
            for column in table.columns:
                for fk in column.foreign_keys:
                    parent = fk.column.table.name
                    if parent != table.name and parent in seen:
                        checks.append((column.name, seen[parent]))
            kept = [
                row for row in rows
                if all(row[name] is None or row[name] in landed for name, landed in checks)
            ]
            if len(kept) < len(rows):
                print(f"{table.name}: skipping {len(rows) - len(kept)} orphaned row(s)")

            if kept:
                # Batched: a settings table is tiny, media can be thousands.
                for start in range(0, len(kept), 500):
                    dst.execute(insert(table), kept[start:start + 500])
            pk = list(table.primary_key.columns)
            if len(pk) == 1:
                seen[table.name] = {row[pk[0].name] for row in kept}
            print(f"{table.name}: {len(kept)} row(s)")
            copied[table.name] = len(kept)
            total += len(kept)

    # Verify outside the write transaction, against what actually landed.
    with target.connect() as dst:
        for table in Base.metadata.sorted_tables:
            b = dst.execute(select(func.count()).select_from(table)).scalar()
            if b != copied[table.name]:
                print(f"MISMATCH in {table.name}: copied {copied[table.name]} vs target {b}")
                return 1
    print(f"Copied and verified {total} row(s). Point DATABASE_URL at the target and restart.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
