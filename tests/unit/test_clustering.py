"""Clustering tests. All data here is synthetic and built inline."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.analysis.clustering import cluster_detections, summarize_events

T0 = pd.Timestamp("2026-08-01T10:00:00Z")
# ~0.0036 deg of latitude is ~400 m: one VIIRS pixel apart.
STEP = 0.0036


def _rows(points):
    return pd.DataFrame(
        [
            {"latitude": lat, "longitude": lon, "acquired_at": t,
             "frp_mw": frp, "scan_km": 0.4, "track_km": 0.4}
            for lat, lon, t, frp in points
        ]
    )


def test_adjacent_pixels_same_pass_form_one_event():
    df = _rows([(10.0 + i * STEP, 20.0, T0, 5.0) for i in range(4)])
    out = cluster_detections(df)
    assert out["event_id"].nunique() == 1


def test_far_apart_pixels_form_separate_events():
    df = _rows([(10.0, 20.0, T0, 5.0), (10.5, 20.0, T0, 5.0)])  # ~55 km apart
    out = cluster_detections(df)
    assert out["event_id"].nunique() == 2


def test_chain_of_neighbours_links_into_one_event():
    # Ends are ~2.8 km apart (> eps) but every step is ~400 m, so DBSCAN chains them.
    df = _rows([(10.0 + i * STEP, 20.0, T0, 1.0) for i in range(8)])
    assert cluster_detections(df)["event_id"].nunique() == 1


def test_same_place_later_pass_within_gap_is_same_event():
    df = _rows([(10.0, 20.0, T0, 5.0), (10.0, 20.0, T0 + pd.Timedelta(hours=12), 5.0)])
    assert cluster_detections(df)["event_id"].nunique() == 1


def test_same_place_after_long_gap_is_new_event():
    df = _rows([(10.0, 20.0, T0, 5.0), (10.0, 20.0, T0 + pd.Timedelta(days=5), 5.0)])
    out = cluster_detections(df)
    assert out["event_id"].nunique() == 2


def test_event_ids_ordered_by_first_seen_and_deterministic():
    later = _rows([(50.0, 50.0, T0 + pd.Timedelta(hours=3), 1.0)])
    earlier = _rows([(-30.0, 140.0, T0, 1.0)])
    df = pd.concat([later, earlier], ignore_index=True)
    out = cluster_detections(df)
    first = out.loc[out["latitude"] == -30.0, "event_id"].item()
    assert first == 0
    assert out["event_id"].tolist() == cluster_detections(df)["event_id"].tolist()


def test_fire_across_dateline_is_one_event_with_correct_centroid():
    df = _rows([(0.0, 179.999, T0, 1.0), (0.0, -179.999, T0, 1.0)])  # ~220 m apart
    out = cluster_detections(df)
    assert out["event_id"].nunique() == 1
    lon = summarize_events(out)["centroid_lon"].item()
    assert abs(abs(lon) - 180.0) < 0.01


def test_empty_input_returns_empty_with_event_id():
    df = pd.DataFrame(columns=["latitude", "longitude", "acquired_at"])
    out = cluster_detections(df)
    assert out.empty and "event_id" in out.columns


def test_missing_column_raises():
    with pytest.raises(ValueError, match="acquired_at"):
        cluster_detections(pd.DataFrame({"latitude": [1.0], "longitude": [1.0]}))


@pytest.mark.parametrize("kwargs", [{"eps_km": 0}, {"max_gap_hours": -1}])
def test_invalid_parameters_raise(kwargs):
    with pytest.raises(ValueError):
        cluster_detections(_rows([(1.0, 1.0, T0, 1.0)]), **kwargs)


def test_summary_values():
    df = _rows([
        (10.0, 20.0, T0, 5.0),
        (10.0 + STEP, 20.0, T0 + pd.Timedelta(hours=6), 7.0),
    ])
    s = summarize_events(cluster_detections(df)).iloc[0]
    assert s["n_detections"] == 2
    assert s["duration_hours"] == pytest.approx(6.0)
    assert s["total_frp_mw"] == pytest.approx(12.0)
    assert s["max_frp_mw"] == pytest.approx(7.0)
    assert s["pixel_footprint_km2"] == pytest.approx(0.32)


def test_missing_frp_stays_nan_not_zero():
    df = _rows([(10.0, 20.0, T0, np.nan)])
    s = summarize_events(cluster_detections(df)).iloc[0]
    assert np.isnan(s["total_frp_mw"])


def test_summarize_requires_event_id():
    with pytest.raises(ValueError, match="event_id"):
        summarize_events(_rows([(1.0, 1.0, T0, 1.0)]))
