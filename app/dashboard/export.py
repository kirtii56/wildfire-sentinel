"""Export fire events as GeoJSON (for QGIS, ArcGIS, Google Earth, geojson.io ...)."""

from __future__ import annotations

import json
import math

import pandas as pd

PROPERTY_COLUMNS = (
    "event_id",
    "n_detections",
    "total_frp_mw",
    "max_frp_mw",
    "duration_hours",
    "pixel_footprint_km2",
    "first_seen",
    "last_seen",
    "region",
    "distance_km",
)


def _clean(value):
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item"):  # NumPy scalar -> Python scalar
        return value.item()
    return value


def events_to_geojson(events: pd.DataFrame) -> str:
    """A GeoJSON FeatureCollection with one Point per fire event."""
    features = []
    for row in events.to_dict("records"):
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    # GeoJSON order is [longitude, latitude].
                    "coordinates": [round(row["centroid_lon"], 6), round(row["centroid_lat"], 6)],
                },
                "properties": {k: _clean(row[k]) for k in PROPERTY_COLUMNS if k in row},
            }
        )
    return json.dumps({"type": "FeatureCollection", "features": features})
