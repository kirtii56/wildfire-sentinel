"""Group fire detections (satellite pixels) into fire events.

Why: NASA FIRMS reports one row per hot pixel per satellite pass. A single real
fire shows up as many adjacent pixels, seen again on later passes. Counting rows
therefore over-counts fires. This module groups rows into "fire events".

How (two steps, both simple enough to explain in an interview):

1. SPACE — DBSCAN with the haversine (great-circle) distance. Two detections are
   neighbours if their centres are within ``eps_km`` of each other. Chains of
   neighbours form one spatial cluster. ``min_samples=1`` means a lone pixel is
   a valid (small) event rather than being thrown away as "noise".

2. TIME — inside each spatial cluster, detections are sorted by time. If the gap
   between two consecutive detections is larger than ``max_gap_hours``, a new
   event starts. So a spot that burned in March and again in August becomes two
   events, not one.

Assumptions, stated plainly (they are tunable parameters, not facts):
- ``eps_km=1.0``: VIIRS I-band pixels are ~375 m at nadir and grow towards the
  swath edge, so neighbouring pixels of one fire are usually well under 1 km apart.
- ``max_gap_hours=24``: each VIIRS satellite passes over most places about twice
  a day, so a burning fire should be re-detected within a day.

Limitations: two separate fires closer than ``eps_km`` merge into one event; a
fire hidden by cloud for longer than ``max_gap_hours`` splits into two. The
output is an estimate of fire events, not an official fire perimeter, and
``pixel_footprint_km2`` is the summed pixel area, not burned area.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN

EARTH_RADIUS_KM = 6371.0088

REQUIRED_COLUMNS = ("latitude", "longitude", "acquired_at")


def cluster_detections(
    detections: pd.DataFrame,
    eps_km: float = 1.0,
    max_gap_hours: float = 24.0,
) -> pd.DataFrame:
    """Return a copy of ``detections`` with an integer ``event_id`` column added.

    Event ids are numbered 0..n-1 in order of each event's first detection time,
    so the same input always produces the same ids.
    """
    if eps_km <= 0:
        raise ValueError("eps_km must be positive")
    if max_gap_hours <= 0:
        raise ValueError("max_gap_hours must be positive")

    missing = [c for c in REQUIRED_COLUMNS if c not in detections.columns]
    if missing:
        raise ValueError(f"detections is missing required column(s): {', '.join(missing)}")

    df = detections.copy()
    df["acquired_at"] = pd.to_datetime(df["acquired_at"], utc=True)

    if df.empty:
        df["event_id"] = pd.Series(dtype="int64")
        return df

    # Step 1: spatial clustering. Haversine in scikit-learn expects [lat, lon]
    # in radians and returns distances in radians, so eps is converted too.
    coords = np.radians(df[["latitude", "longitude"]].astype(float).to_numpy())
    spatial = DBSCAN(
        eps=eps_km / EARTH_RADIUS_KM,
        min_samples=1,
        metric="haversine",
        algorithm="ball_tree",
    ).fit_predict(coords)
    df["_spatial_cluster"] = spatial

    # Step 2: split each spatial cluster wherever the time gap is too large.
    df = df.sort_values(["_spatial_cluster", "acquired_at"], kind="stable")
    gap = df.groupby("_spatial_cluster")["acquired_at"].diff()
    new_segment = gap.isna() | (gap > pd.Timedelta(hours=max_gap_hours))
    df["_segment"] = new_segment.groupby(df["_spatial_cluster"]).cumsum()

    # Deterministic ids: order events by their first detection time.
    keys = df.groupby(["_spatial_cluster", "_segment"])["acquired_at"].transform("min")
    df["_first_seen"] = keys
    order = (
        df.drop_duplicates(["_spatial_cluster", "_segment"])
        .sort_values(["_first_seen", "latitude", "longitude"], kind="stable")[
            ["_spatial_cluster", "_segment"]
        ]
        .reset_index(drop=True)
    )
    order["event_id"] = np.arange(len(order), dtype="int64")
    df = df.merge(order, on=["_spatial_cluster", "_segment"], how="left")

    return df.drop(columns=["_spatial_cluster", "_segment", "_first_seen"])


def summarize_events(clustered: pd.DataFrame) -> pd.DataFrame:
    """One row per fire event, with size, timing and intensity.

    Columns: event_id, n_detections, centroid_lat, centroid_lon, first_seen,
    last_seen, duration_hours, total_frp_mw, max_frp_mw, pixel_footprint_km2.
    FRP and footprint are NaN when the underlying values are missing, never 0.
    """
    if "event_id" not in clustered.columns:
        raise ValueError("run cluster_detections() first: 'event_id' column missing")

    df = clustered.copy()
    for col in ("frp_mw", "scan_km", "track_km"):
        if col not in df.columns:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["_pixel_km2"] = df["scan_km"] * df["track_km"]
    # Circular mean of longitude (so a fire straddling the 180° line isn't placed at 0°),
    # computed for all events at once from per-row sin/cos: no Python loop per event.
    lon_rad = np.radians(df["longitude"].astype(float))
    df["_sin_lon"] = np.sin(lon_rad)
    df["_cos_lon"] = np.cos(lon_rad)
    df["_lat"] = df["latitude"].astype(float)

    grouped = df.groupby("event_id")
    summary = grouped.agg(
        n_detections=("_lat", "size"),
        centroid_lat=("_lat", "mean"),
        _sin=("_sin_lon", "mean"),
        _cos=("_cos_lon", "mean"),
        first_seen=("acquired_at", "min"),
        last_seen=("acquired_at", "max"),
        max_frp_mw=("frp_mw", "max"),
    )
    # min_count=1 keeps "all values missing" as NaN instead of 0.
    summary["total_frp_mw"] = grouped["frp_mw"].sum(min_count=1)
    summary["pixel_footprint_km2"] = grouped["_pixel_km2"].sum(min_count=1)
    summary["centroid_lon"] = np.degrees(np.arctan2(summary["_sin"], summary["_cos"]))
    summary = summary.drop(columns=["_sin", "_cos"]).reset_index()
    summary["duration_hours"] = (
        summary["last_seen"] - summary["first_seen"]
    ).dt.total_seconds() / 3600.0

    columns = [
        "event_id",
        "n_detections",
        "centroid_lat",
        "centroid_lon",
        "first_seen",
        "last_seen",
        "duration_hours",
        "total_frp_mw",
        "max_frp_mw",
        "pixel_footprint_km2",
    ]
    return summary[columns].sort_values("event_id").reset_index(drop=True)
