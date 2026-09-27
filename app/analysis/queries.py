"""Read detections from Postgres into a pandas DataFrame for analysis."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import psycopg

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
) -> pd.DataFrame:
    """Detections with ``start <= acquired_at < end``, optionally inside a bbox.

    ``bbox`` is (lon_min, lat_min, lon_max, lat_max), the same order FIRMS uses.
    """
    sql = f"""
        SELECT {", ".join(DETECTION_COLUMNS)}
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
    sql += " ORDER BY acquired_at"

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    df = pd.DataFrame(rows, columns=list(DETECTION_COLUMNS))
    # Postgres NUMERIC arrives as Decimal; analysis code expects floats.
    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    df["acquired_at"] = pd.to_datetime(df["acquired_at"], utc=True)
    return df
