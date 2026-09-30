"""Tests for the dashboard's pure helpers (geo, trends, export, share links)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from app.dashboard.export import events_to_geojson
from app.dashboard.geo import circle_points, format_latlon, haversine_km, label_regions
from app.dashboard.share import ViewState, from_query, share_url, to_query
from app.dashboard.trends import format_delta, pct_change, split_periods

NOW = pd.Timestamp("2026-09-30T03:00:00Z")


# ---------- geo ----------
def test_format_latlon_hemispheres():
    assert format_latlon(-12.38, 133.25) == "12.38°S, 133.25°E"
    assert format_latlon(40.0, -3.7) == "40.00°N, 3.70°W"


def test_haversine_known_distance():
    # London -> Paris is about 344 km.
    assert haversine_km(51.5074, -0.1278, 48.8566, 2.3522) == pytest.approx(344, abs=3)
    assert haversine_km(10, 20, 10, 20) == pytest.approx(0)


def test_haversine_vectorised():
    d = haversine_km(np.array([0.0, 0.0]), np.array([0.0, 0.0]), 0.0, np.array([1.0, 2.0]))
    assert d[1] == pytest.approx(2 * d[0], rel=1e-3)


def test_circle_points_are_at_the_radius():
    lats, lons = circle_points(-11.4, -43.5, 50)
    distances = haversine_km(-11.4, -43.5, np.array(lats), np.array(lons))
    assert np.allclose(distances, 50, atol=0.01)


def test_circle_across_dateline_stays_in_range():
    _, lons = circle_points(0, 179.9, 100)
    assert all(-180 <= lon <= 180 for lon in lons)


def test_region_labels():
    lat = pd.Series([-11.4, 48.8, -25.0, 0.0])
    lon = pd.Series([-43.5, 2.3, 133.0, -140.0])
    assert list(label_regions(lat, lon)) == [
        "South America",
        "Europe",
        "Australia & Oceania",
        "Other",
    ]


# ---------- trends ----------
def test_split_periods():
    hours = [1, 10, 30, 40, 60]
    df = pd.DataFrame({"acquired_at": [NOW - pd.Timedelta(hours=h) for h in hours]})
    current, previous = split_periods(df, days=1, now=NOW)
    assert len(current) == 2 and len(previous) == 2  # 60 h ago is outside both


def test_pct_change_and_format():
    assert pct_change(118, 100) == pytest.approx(18)
    assert pct_change(5, 0) is None
    assert format_delta(18.4, "24 h") == "+18% vs previous 24 h"
    assert format_delta(-7.6, "48 h") == "-8% vs previous 48 h"
    assert format_delta(None, "24 h") is None


# ---------- export ----------
def test_geojson_export():
    events = pd.DataFrame(
        {
            "event_id": [0],
            "centroid_lat": [-11.4],
            "centroid_lon": [-43.5],
            "n_detections": [3],
            "total_frp_mw": [float("nan")],
            "max_frp_mw": [4.0],
            "duration_hours": [1.5],
            "first_seen": [NOW],
            "last_seen": [NOW],
            "region": ["South America"],
        }
    )
    data = json.loads(events_to_geojson(events))
    feature = data["features"][0]
    assert data["type"] == "FeatureCollection"
    assert feature["geometry"]["coordinates"] == [-43.5, -11.4]  # lon, lat
    assert feature["properties"]["total_frp_mw"] is None  # NaN -> null
    assert feature["properties"]["first_seen"].startswith("2026-09-30T03:00")


# ---------- share links ----------
def test_query_round_trip():
    state = ViewState(
        region="Africa", window="72h", view="globe", near_lat=-11.4, near_lon=-43.5, radius_km=250
    )
    assert from_query(to_query(state)) == ViewState(
        region="Africa",
        window="72h",
        view="globe",
        near_lat=-11.4,
        near_lon=-43.5,
        radius_km=250,
    )


def test_invalid_query_values_are_ignored():
    state = from_query({"region": "Mars", "window": "9d", "lat": "abc", "lon": "5", "radius": "7"})
    assert state == ViewState()


def test_default_state_gives_clean_url():
    assert share_url("https://wildfire-sentinel.streamlit.app/?x=1", ViewState()) == (
        "https://wildfire-sentinel.streamlit.app/"
    )
    url = share_url("https://a.app/", ViewState(region="Asia"))
    assert url == "https://a.app/?region=Asia"
