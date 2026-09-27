"""API tests against a real, disposable Postgres (skipped without TEST_DATABASE_URL).

All rows are synthetic and inserted by the tests themselves.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.main import app, get_database_url

NOW = datetime.now(UTC).replace(microsecond=0)
STEP = 0.0036  # ~400 m of latitude


def _insert_run(db) -> int:
    row = db.execute(
        """INSERT INTO ingest_runs (product, area, day_range, request_url, status,
               rows_fetched, rows_inserted, finished_at)
           VALUES ('VIIRS_NOAA20_NRT', 'world', 1, 'https://example/REDACTED', 'success',
               5, 5, now()) RETURNING id"""
    ).fetchone()
    return row["id"]


def _insert_detection(db, run_id, lat, lon, when, frp, product="VIIRS_NOAA20_NRT"):
    db.execute(
        """INSERT INTO fire_detections (ingest_run_id, product, instrument, satellite,
               latitude, longitude, acq_date, acq_time_utc, frp_mw, scan_km, track_km,
               confidence_text, daynight)
           VALUES (%s, %s, 'VIIRS', 'N20', %s, %s, %s, %s, %s, 0.4, 0.4, 'nominal', 'D')""",
        (run_id, product, lat, lon, when.date(), when.time(), frp),
    )


@pytest.fixture
def client(db, migrated_database):
    run = _insert_run(db)
    t = NOW - timedelta(hours=2)
    # Event A: three adjacent pixels in one pass (big fire).
    for i in range(3):
        _insert_detection(db, run, 10.0 + i * STEP, 20.0, t, 30.0)
    # Event B: one pixel far away (small fire), missing FRP.
    _insert_detection(db, run, -30.0, 140.0, t, None)
    # Old detection, outside a 1-day window.
    _insert_detection(db, run, 50.0, 50.0, NOW - timedelta(days=5), 99.0)
    db.commit()

    app.dependency_overrides[get_database_url] = lambda: migrated_database
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "database": "ok"}


def test_root_redirects_to_docs(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/docs"


def test_detections_respects_time_window(client):
    assert len(client.get("/detections?days=1").json()) == 4
    assert len(client.get("/detections?days=7").json()) == 5


def test_detections_bbox_and_limit(client):
    rows = client.get("/detections?days=1&bbox=19,9,21,11").json()
    assert len(rows) == 3 and all(9 <= r["latitude"] <= 11 for r in rows)
    assert len(client.get("/detections?days=1&limit=2").json()) == 2


@pytest.mark.parametrize("bbox", ["1,2,3", "a,b,c,d", "10,0,5,5", "0,0,200,10"])
def test_bad_bbox_rejected(client, bbox):
    assert client.get(f"/detections?bbox={bbox}").status_code == 422


def test_fires_clusters_and_sorts(client):
    body = client.get("/fires?days=1").json()
    assert body["total_detections"] == 4
    assert body["total_events"] == 2
    big, small = body["events"]
    assert big["n_detections"] == 3 and big["total_frp_mw"] == pytest.approx(90.0)
    assert small["total_frp_mw"] is None  # missing FRP stays null, never 0


def test_fires_min_detections_filter(client):
    body = client.get("/fires?days=1&min_detections=2").json()
    assert body["total_events"] == 2 and len(body["events"]) == 1


def test_top_fires(client):
    top = client.get("/fires/top?n=1").json()
    assert len(top) == 1 and top[0]["n_detections"] == 3


def test_daily_stats(client):
    stats = client.get("/stats/daily?days=7").json()
    assert sum(s["detections"] for s in stats) == 5
    assert {s["product"] for s in stats} == {"VIIRS_NOAA20_NRT"}


def test_ingest_runs(client):
    runs = client.get("/ingest-runs").json()
    assert len(runs) == 1 and runs[0]["status"] == "success"


def test_empty_window_returns_empty(client):
    body = client.get("/fires?days=1&bbox=-10,-10,-9,-9").json()
    assert body["total_events"] == 0 and body["events"] == []


def test_invalid_days_rejected(client):
    assert client.get("/fires?days=0").status_code == 422
    assert client.get("/fires?days=31").status_code == 422
