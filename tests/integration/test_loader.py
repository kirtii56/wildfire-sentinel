from __future__ import annotations

import psycopg
import pytest

from app.database import loader
from app.ingestion.parser import parse_csv
from app.ingestion.products import get_product
from app.ingestion.validation import validate_records

SPEC = get_product("VIIRS_NOAA20_NRT")


@pytest.fixture
def run_id(db) -> int:
    return loader.start_ingest_run(
        db,
        product=SPEC.key,
        area="68,6,98,38",
        day_range=1,
        request_url="https://firms.modaps.eosdis.nasa.gov/api/area/csv/REDACTED/"
        "VIIRS_NOAA20_NRT/68,6,98,38/1",
    )


@pytest.fixture
def valid_records(fixtures_dir):
    raw = parse_csv((fixtures_dir / "synthetic_viirs_valid.csv").read_text(), SPEC)
    return validate_records(raw, SPEC).accepted


def test_batch_insert_stores_every_record_with_provenance(db, run_id, valid_records):
    result = loader.insert_detections(db, run_id, valid_records)

    assert result.attempted == 4
    assert result.inserted == 4
    assert result.skipped_duplicate == 0

    stored = db.execute(
        "SELECT count(*) AS n, count(DISTINCT ingest_run_id) AS runs FROM fire_detections"
    ).fetchone()
    assert stored["n"] == 4
    assert stored["runs"] == 1

    row = db.execute("SELECT source, ingest_run_id FROM fire_detections LIMIT 1").fetchone()
    assert row["source"] == "nasa_firms"
    assert row["ingest_run_id"] == run_id


def test_reinserting_the_same_observations_is_absorbed_by_the_constraint(db, run_id, valid_records):
    loader.insert_detections(db, run_id, valid_records)
    second = loader.insert_detections(db, run_id, valid_records)

    assert second.attempted == 4
    assert second.inserted == 0
    assert second.skipped_duplicate == 4
    assert db.execute("SELECT count(*) AS n FROM fire_detections").fetchone()["n"] == 4


def test_duplicates_within_a_single_batch_are_also_absorbed(db, run_id, valid_records):
    result = loader.insert_detections(db, run_id, valid_records + valid_records)

    assert result.attempted == 8
    assert result.inserted == 4
    assert db.execute("SELECT count(*) AS n FROM fire_detections").fetchone()["n"] == 4


def test_a_different_pixel_at_the_same_time_is_kept_not_deduplicated(db, run_id, valid_records):
    """Deduplication is observation-level. Neighbouring pixels are distinct data."""
    loader.insert_detections(db, run_id, valid_records)
    neighbour = dict(valid_records[0])
    neighbour["latitude"] = neighbour["latitude"] + 0.003

    result = loader.insert_detections(db, run_id, [neighbour])

    assert result.inserted == 1
    assert db.execute("SELECT count(*) AS n FROM fire_detections").fetchone()["n"] == 5


def test_confidence_must_be_exactly_one_of_the_two_representations(db, run_id, valid_records):
    both = dict(valid_records[0])
    both["confidence_pct"] = 80
    with pytest.raises(psycopg.errors.CheckViolation):
        loader.insert_detections(db, run_id, [both])
    db.rollback()

    neither = dict(valid_records[1])
    neither["confidence_text"] = None
    with pytest.raises(psycopg.errors.CheckViolation):
        loader.insert_detections(db, run_id, [neither])


def test_brightness_without_its_channel_is_refused(db, run_id, valid_records):
    orphaned = dict(valid_records[0])
    orphaned["brightness_channel"] = None
    with pytest.raises(psycopg.errors.CheckViolation):
        loader.insert_detections(db, run_id, [orphaned])


def test_out_of_range_coordinates_are_refused_by_the_database_too(db, run_id, valid_records):
    bad = dict(valid_records[0])
    bad["latitude"] = 99.0
    with pytest.raises(psycopg.errors.CheckViolation):
        loader.insert_detections(db, run_id, [bad])


def test_rejects_are_stored_with_their_reason_and_raw_row(db, run_id, fixtures_dir):
    raw = parse_csv((fixtures_dir / "synthetic_viirs_invalid.csv").read_text(), SPEC)
    outcome = validate_records(raw, SPEC)

    stored = loader.insert_rejects(db, run_id, outcome.rejected)

    assert stored == 9
    rows = db.execute("SELECT reason, raw_row FROM ingest_rejects ORDER BY row_number").fetchall()
    assert len(rows) == 9
    assert "latitude" in rows[0]["reason"]
    assert rows[0]["raw_row"]["longitude"] == "-67.890000"


def test_finish_ingest_run_records_the_real_counts(db, run_id, valid_records):
    loader.insert_detections(db, run_id, valid_records)
    loader.finish_ingest_run(
        db, run_id, status="success", rows_fetched=4, rows_accepted=4, rows_inserted=4
    )

    run = db.execute("SELECT * FROM ingest_runs WHERE id = %s", (run_id,)).fetchone()
    assert run["status"] == "success"
    assert run["rows_inserted"] == 4
    assert run["finished_at"] is not None
    assert "REDACTED" in run["request_url"]
