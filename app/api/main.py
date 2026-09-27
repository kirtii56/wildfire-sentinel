"""Wildfire Sentinel REST API.

Run locally:   uvicorn app.api.main:app --reload
Interactive docs are served at /docs.

Only DATABASE_URL is needed. The API reads what the ingest job stored; it never
calls NASA itself.
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from psycopg.rows import dict_row

from app.analysis.clustering import cluster_detections, summarize_events
from app.analysis.queries import load_detections
from app.api.schemas import DailyStat, Detection, FireEvent, FireEventList, Health, IngestRun

app = FastAPI(
    title="Wildfire Sentinel API",
    version="2.0.0",
    description=(
        "NASA FIRMS VIIRS active-fire detections, grouped into fire events. "
        "Fire events are estimates from DBSCAN clustering, not official fire perimeters."
    ),
)

BBox = tuple[float, float, float, float]


def get_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not configured")
    return url


def get_conn(
    database_url: Annotated[str, Depends(get_database_url)],
) -> Iterator[psycopg.Connection]:
    try:
        conn = psycopg.connect(database_url, row_factory=dict_row, connect_timeout=5)
    except psycopg.OperationalError as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    with conn:
        yield conn


Conn = Annotated[psycopg.Connection, Depends(get_conn)]
DaysParam = Annotated[int, Query(ge=1, le=30, description="Look back this many days")]
BBoxParam = Annotated[
    str | None,
    Query(description="lon_min,lat_min,lon_max,lat_max (same order as NASA FIRMS)"),
]


def parse_bbox(bbox: str | None) -> BBox | None:
    if bbox is None:
        return None
    try:
        lon_min, lat_min, lon_max, lat_max = (float(v) for v in bbox.split(","))
    except ValueError:
        raise HTTPException(
            422, "bbox must be four numbers: lon_min,lat_min,lon_max,lat_max"
        ) from None
    if not (-180 <= lon_min < lon_max <= 180 and -90 <= lat_min < lat_max <= 90):
        raise HTTPException(422, "bbox is out of range or min >= max")
    return lon_min, lat_min, lon_max, lat_max


def window(days: int) -> tuple[datetime, datetime]:
    end = datetime.now(UTC)
    return end - timedelta(days=days), end


def _none_if_nan(value):
    return None if isinstance(value, float) and math.isnan(value) else value


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/docs")


@app.get("/health", response_model=Health, tags=["system"])
def health(conn: Conn) -> Health:
    conn.execute("SELECT 1")
    return Health(status="ok", database="ok")


@app.get("/detections", response_model=list[Detection], tags=["data"])
def detections(
    conn: Conn,
    days: DaysParam = 1,
    bbox: BBoxParam = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 1000,
) -> list[Detection]:
    """Raw satellite detections (one row per hot pixel per pass), newest first."""
    start, end = window(days)
    df = load_detections(conn, start, end, parse_bbox(bbox), limit=limit)
    records = df.astype(object).where(df.notna(), None).to_dict("records")
    return [Detection(**r) for r in records]


@app.get("/fires", response_model=FireEventList, tags=["fires"])
def fires(
    conn: Conn,
    days: DaysParam = 1,
    bbox: BBoxParam = None,
    min_detections: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
    eps_km: Annotated[float, Query(gt=0, le=10)] = 1.0,
    max_gap_hours: Annotated[float, Query(gt=0, le=240)] = 24.0,
) -> FireEventList:
    """Detections grouped into fire events, largest total fire power first."""
    start, end = window(days)
    df = load_detections(conn, start, end, parse_bbox(bbox))
    if df.empty:
        return FireEventList(
            window_start=start, window_end=end, total_detections=0, total_events=0, events=[]
        )
    events = summarize_events(cluster_detections(df, eps_km=eps_km, max_gap_hours=max_gap_hours))
    total_events = len(events)
    events = events[events["n_detections"] >= min_detections]
    events = events.sort_values("total_frp_mw", ascending=False, na_position="last").head(limit)
    items = [
        FireEvent(**{k: _none_if_nan(v) for k, v in row.items()})
        for row in events.to_dict("records")
    ]
    return FireEventList(
        window_start=start,
        window_end=end,
        total_detections=len(df),
        total_events=total_events,
        events=items,
    )


@app.get("/fires/top", response_model=list[FireEvent], tags=["fires"])
def top_fires(
    conn: Conn,
    days: DaysParam = 1,
    n: Annotated[int, Query(ge=1, le=100)] = 10,
) -> list[FireEvent]:
    """The n fire events with the highest total fire radiative power."""
    return fires(
        conn, days=days, bbox=None, min_detections=1, limit=n, eps_km=1.0, max_gap_hours=24.0
    ).events


@app.get("/stats/daily", response_model=list[DailyStat], tags=["stats"])
def daily_stats(conn: Conn, days: DaysParam = 7) -> list[DailyStat]:
    """Detections and total FRP per UTC day and satellite product (plain SQL aggregate)."""
    start, _ = window(days)
    rows = conn.execute(
        """
        SELECT acq_date AS day, product,
               COUNT(*) AS detections,
               SUM(frp_mw)::float AS total_frp_mw
        FROM fire_detections
        WHERE acquired_at >= %s
        GROUP BY acq_date, product
        ORDER BY acq_date, product
        """,
        (start,),
    ).fetchall()
    return [DailyStat(**r) for r in rows]


@app.get("/ingest-runs", response_model=list[IngestRun], tags=["system"])
def ingest_runs(conn: Conn, limit: Annotated[int, Query(ge=1, le=100)] = 10) -> list[IngestRun]:
    """Most recent ingest runs, to see how fresh the data is."""
    rows = conn.execute(
        """
        SELECT id, product, area, status, started_at, finished_at, rows_fetched,
               rows_inserted, rows_duplicate, rows_rejected
        FROM ingest_runs ORDER BY started_at DESC LIMIT %s
        """,
        (limit,),
    ).fetchall()
    return [IngestRun(**r) for r in rows]
