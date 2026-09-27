from __future__ import annotations

import psycopg
import pytest

from scripts.migrate import apply_migrations, discover_migrations


def test_migrations_create_the_expected_tables(migrated_database):
    with psycopg.connect(migrated_database) as conn:
        rows = conn.execute(
            """
            SELECT table_name FROM information_schema.tables
             WHERE table_schema = 'public' ORDER BY table_name
            """
        ).fetchall()
    names = {row[0] for row in rows}
    assert {"fire_detections", "ingest_runs", "ingest_rejects", "schema_migrations"} <= names


def test_reapplying_migrations_is_a_no_op(migrated_database):
    assert apply_migrations(migrated_database) == []


def test_editing_an_applied_migration_is_refused(migrated_database, tmp_path, monkeypatch):
    original = discover_migrations()[0]
    tampered = tmp_path / original.name
    tampered.write_text(original.read_text() + "\n-- changed after the fact\n")
    monkeypatch.setattr("scripts.migrate.discover_migrations", lambda *_a, **_k: [tampered])

    with pytest.raises(RuntimeError, match="immutable"):
        apply_migrations(migrated_database)


def test_acquired_at_is_generated_from_date_and_utc_time(db):
    db.execute(
        """
        INSERT INTO ingest_runs (product, area, day_range, request_url, status)
        VALUES ('VIIRS_NOAA20_NRT', 'test', 1, 'https://example.invalid/REDACTED', 'running')
        """
    )
    run_id = db.execute("SELECT id FROM ingest_runs LIMIT 1").fetchone()["id"]
    db.execute(
        """
        INSERT INTO fire_detections
            (ingest_run_id, product, instrument, satellite, latitude, longitude,
             acq_date, acq_time_utc, confidence_text)
        VALUES (%s, 'VIIRS_NOAA20_NRT', 'VIIRS', 'N20', -12.345, -67.89,
                '2026-06-12', '13:45', 'nominal')
        """,
        (run_id,),
    )
    acquired = db.execute("SELECT acquired_at FROM fire_detections").fetchone()["acquired_at"]
    assert acquired.isoformat() == "2026-06-12T13:45:00+00:00"
