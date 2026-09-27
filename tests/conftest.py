from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures"

from scripts.migrate import apply_migrations  # noqa: E402

TABLES = ("ingest_rejects", "fire_detections", "ingest_runs")


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set; skipping database integration tests")
    return url


@pytest.fixture(scope="session")
def migrated_database(test_database_url: str) -> str:
    """Apply migrations once per session against the disposable test database."""
    apply_migrations(test_database_url)
    return test_database_url


@pytest.fixture
def db(migrated_database: str):
    """A connection to the test database, truncated before each test.

    The loader commits, so per-test isolation is done by truncation rather than
    by rolling back a surrounding transaction.
    """
    with psycopg.connect(migrated_database, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
        conn.commit()
        yield conn
