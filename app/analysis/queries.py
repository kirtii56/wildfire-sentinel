"""Read detections from Postgres into a pandas DataFrame for analysis."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import psycopg
from psycopg.rows import tuple_row

DETECTION_COLUMNS = (
    "id", "latitude", "longitude", "acquired_at", "satellite", "product",
    "frp_mw", "scan_km", "track_km", "confidence_text", "daynight",
)

NUMERIC_COLUMNS = ("latitude", "longitude", "frp_mw", "scan_km", "track_km")


def load_detections(
    conn: psycopg.Connection,
    start: datetime,
    end: datetime,
    bbox: tuple[float, float, float, float] | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    """Detections with ``start <= acquired_at < end``, optionally inside a bbox.

    ``bbox`` is (lon_min, lat_min, lon_max, lat_max), the same order FIRMS uses.
    With ``limit``, only the newest ``limit`` rows are returned (newest first).
    """
    # NUMERIC columns are cast to float8 in SQL: converting Python Decimals in
    # pandas is far slower for world-scale result sets.
    select = ", ".join(
        f"{c}::float8 AS {c}" if c in NUMERIC_COLUMNS else c for c in DETECTION_COLUMNS
    )
    sql = f"""
        SELECT {select}
        FROM fire_detections
        WHERE acquired_at >= %(start)s AND acquired_at < %(end)s
    """
    params: dict[str, object] = {"start": start, "end": end}
    if bbox is not None:
        lon_min, lat_min, lon_max, lat_max = bbox
        sql += """
          AND longitude BETWEEN %(lon_min)s AND %(lon_max)s
          AND latitude  BETWEEN %(lat_min)s AND %(lat_max)s
        """
        params.update(lon_min=lon_min, lon_max=lon_max, lat_min=lat_min, lat_max=lat_max)
    if limit is None:
        sql += " ORDER BY acquired_at"
    else:
        sql += " ORDER BY acquired_at DESC LIMIT %(limit)s"
        params["limit"] = limit

    # Plain tuples are much faster to build than dicts for large result sets.
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    df = pd.DataFrame(rows, columns=list(DETECTION_COLUMNS))
    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    df["acquired_at"] = pd.to_datetime(df["acquired_at"], utc=True)
    return df
