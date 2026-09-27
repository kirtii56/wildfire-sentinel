from __future__ import annotations

import httpx
import respx

from app.ingestion.firms_client import FirmsClient, build_url
from app.ingestion.pipeline import ingest_product

MAP_KEY = "test_map_key_do_not_use_0123456789"
PRODUCT = "VIIRS_NOAA20_NRT"
AREA = "68,6,98,38"


def _client() -> FirmsClient:
    return FirmsClient(map_key=MAP_KEY, sleep=lambda _s: None)


def _mock(csv_text: str, status: int = 200):
    return respx.get(build_url(MAP_KEY, PRODUCT, AREA, 1)).mock(
        return_value=httpx.Response(status, text=csv_text)
    )


@respx.mock
def test_end_to_end_run_stores_data_and_its_provenance(db, fixtures_dir):
    _mock((fixtures_dir / "synthetic_viirs_valid.csv").read_text())

    summary = ingest_product(db, _client(), product=PRODUCT, area=AREA, day_range=1)

    assert summary.status == "success"
    assert (summary.rows_fetched, summary.rows_accepted, summary.rows_inserted) == (4, 4, 4)
    assert summary.rows_rejected == 0

    run = db.execute("SELECT * FROM ingest_runs WHERE id = %s", (summary.run_id,)).fetchone()
    assert run["status"] == "success"
    assert run["rows_inserted"] == 4
    assert MAP_KEY not in run["request_url"]
    assert "REDACTED" in run["request_url"]

    detections = db.execute("SELECT count(*) AS n FROM fire_detections").fetchone()
    assert detections["n"] == 4


@respx.mock
def test_rerunning_the_same_window_inserts_nothing_new(db, fixtures_dir):
    _mock((fixtures_dir / "synthetic_viirs_valid.csv").read_text())

    first = ingest_product(db, _client(), product=PRODUCT, area=AREA, day_range=1)
    second = ingest_product(db, _client(), product=PRODUCT, area=AREA, day_range=1)

    assert first.rows_inserted == 4
    assert second.rows_inserted == 0
    assert second.rows_duplicate == 4
    assert second.status == "success"
    assert db.execute("SELECT count(*) AS n FROM fire_detections").fetchone()["n"] == 4


@respx.mock
def test_invalid_rows_are_rejected_recorded_and_reported_as_partial(db, fixtures_dir):
    _mock((fixtures_dir / "synthetic_viirs_invalid.csv").read_text())

    summary = ingest_product(db, _client(), product=PRODUCT, area=AREA, day_range=1)

    assert summary.status == "partial"
    assert summary.rows_fetched == 9
    assert summary.rows_accepted == 0
    assert summary.rows_rejected == 9
    assert summary.rows_inserted == 0
    assert db.execute("SELECT count(*) AS n FROM fire_detections").fetchone()["n"] == 0
    assert db.execute("SELECT count(*) AS n FROM ingest_rejects").fetchone()["n"] == 9


@respx.mock
def test_a_failed_fetch_is_recorded_and_stores_no_detections(db):
    _mock("Invalid MAP_KEY.")

    summary = ingest_product(db, _client(), product=PRODUCT, area=AREA, day_range=1)

    assert summary.status == "failed"
    assert summary.error_detail is not None
    assert MAP_KEY not in summary.error_detail

    run = db.execute("SELECT * FROM ingest_runs WHERE id = %s", (summary.run_id,)).fetchone()
    assert run["status"] == "failed"
    assert run["rows_inserted"] == 0
    assert db.execute("SELECT count(*) AS n FROM fire_detections").fetchone()["n"] == 0


@respx.mock
def test_an_empty_but_valid_response_is_a_success_not_a_failure(db):
    header = (
        "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
        "instrument,confidence,version,bright_ti5,frp,daynight\n"
    )
    _mock(header)

    summary = ingest_product(db, _client(), product=PRODUCT, area=AREA, day_range=1)

    assert summary.status == "success"
    assert summary.rows_fetched == 0
    assert summary.rows_inserted == 0
