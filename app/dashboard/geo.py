"""Regions, distances and coordinate formatting."""

from __future__ import annotations

import numpy as np
import pandas as pd

EARTH_RADIUS_KM = 6371.0088

# Bounding boxes: (lon_min, lat_min, lon_max, lat_max). They overlap at the edges,
# so region labels are approximate; when labelling, the first match in LABEL_ORDER wins.
REGIONS: dict[str, tuple[float, float, float, float] | None] = {
    "World": None,
    "Africa": (-20.0, -36.0, 55.0, 38.0),
    "Asia": (25.0, -11.0, 180.0, 82.0),
    "Australia & Oceania": (110.0, -50.0, 180.0, 0.0),
    "Europe": (-25.0, 34.0, 45.0, 72.0),
    "North America": (-170.0, 7.0, -50.0, 84.0),
    "South America": (-92.0, -56.0, -30.0, 13.0),
}
LABEL_ORDER = (
    "Europe",
    "Africa",
    "South America",
    "North America",
    "Australia & Oceania",
    "Asia",
)


def in_region(lat: pd.Series, lon: pd.Series, region: str) -> pd.Series:
    """Boolean mask of points inside a named region ("World" keeps everything)."""
    bbox = REGIONS[region]
    if bbox is None:
        return pd.Series(True, index=lat.index)
    lon_min, lat_min, lon_max, lat_max = bbox
    return lon.between(lon_min, lon_max) & lat.between(lat_min, lat_max)


def label_regions(lat: pd.Series, lon: pd.Series) -> np.ndarray:
    """Approximate continent for each point."""
    conditions = [in_region(lat, lon, name) for name in LABEL_ORDER]
    return np.select(conditions, list(LABEL_ORDER), default="Other")


def format_latlon(lat: float, lon: float) -> str:
    """12.38°S, 133.25°E"""
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.2f}°{ns}, {abs(lon):.2f}°{ew}"


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km. Works on scalars or NumPy/pandas arrays."""
    lat1, lon1, lat2, lon2 = (
        np.radians(np.asarray(v, dtype=float)) for v in (lat1, lon1, lat2, lon2)
    )
    a = (
        np.sin((lat2 - lat1) / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def circle_points(lat: float, lon: float, radius_km: float, n: int = 90) -> tuple[list, list]:
    """Latitudes and longitudes tracing a circle of ``radius_km`` around a point."""
    lat_r, lon_r = np.radians(lat), np.radians(lon)
    d = radius_km / EARTH_RADIUS_KM
    bearings = np.linspace(0, 2 * np.pi, n)
    lat2 = np.arcsin(np.sin(lat_r) * np.cos(d) + np.cos(lat_r) * np.sin(d) * np.cos(bearings))
    lon2 = lon_r + np.arctan2(
        np.sin(bearings) * np.sin(d) * np.cos(lat_r), np.cos(d) - np.sin(lat_r) * np.sin(lat2)
    )
    lon_deg = (np.degrees(lon2) + 540) % 360 - 180
    return np.degrees(lat2).tolist(), lon_deg.tolist()
