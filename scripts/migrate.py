"""Apply numbered SQL migrations from migrations/ in filename order.

Each file is applied once and recorded in schema_migrations. Files are expected
to manage their own transaction (BEGIN/COMMIT) or be safely autocommittable.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

CREATE_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT        PRIMARY KEY,
    sha256      TEXT        NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    return sorted(p for p in directory.glob("*.sql") if p.is_file())


def applied_migrations(conn: psycopg.Connection) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(CREATE_TRACKING_TABLE)
        cur.execute("SELECT filename, sha256 FROM schema_migrations;")
        return dict(cur.fetchall())


def apply_migrations(database_url: str, dry_run: bool = False) -> list[str]:
    """Apply pending migrations. Returns the filenames applied."""
    newly_applied: list[str] = []
    with psycopg.connect(database_url, autocommit=True) as conn:
        already = applied_migrations(conn)
        for path in discover_migrations():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if path.name in already:
                if already[path.name] != digest:
                    raise RuntimeError(
                        f"{path.name} was already applied but its contents have "
                        f"changed. Migrations are immutable once applied; add a "
                        f"new numbered file instead."
                    )
                continue
            if dry_run:
                newly_applied.append(path.name)
                continue
            conn.execute(path.read_text())
            conn.execute(
                "INSERT INTO schema_migrations (filename, sha256) VALUES (%s, %s);",
                (path.name, digest),
            )
            newly_applied.append(path.name)
    return newly_applied


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply SQL migrations.")
    parser.add_argument(
        "--database-url",
        help="Postgres connection string. Defaults to DATABASE_URL from the environment/.env.",
    )
    parser.add_argument("--dry-run", action="store_true", help="List pending migrations only.")
    args = parser.parse_args()

    database_url = args.database_url
    if not database_url:
        from app.core.config import get_settings

        database_url = get_settings().database_url

    pending = apply_migrations(database_url, dry_run=args.dry_run)
    if not pending:
        print("No pending migrations.")
    else:
        verb = "Pending" if args.dry_run else "Applied"
        for name in pending:
            print(f"{verb}: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
