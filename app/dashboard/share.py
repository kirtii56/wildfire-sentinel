"""Read and write the dashboard state in the URL, so a view can be shared as a link."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from app.dashboard.geo import REGIONS

WINDOWS = {"24h": 1, "48h": 2, "72h": 3}
VIEWS = ("map", "globe")
RADII_KM = (25, 50, 100, 250, 500)


@dataclass
class ViewState:
    region: str = "World"
    window: str = "24h"
    view: str = "map"
    near_lat: float | None = None
    near_lon: float | None = None
    radius_km: int = 100

    @property
    def near_active(self) -> bool:
        return self.near_lat is not None and self.near_lon is not None


def _float_in(value, low: float, high: float) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


def from_query(params: dict) -> ViewState:
    """Build a state from URL parameters, ignoring anything invalid."""
    state = ViewState()
    if params.get("region") in REGIONS:
        state.region = params["region"]
    if params.get("window") in WINDOWS:
        state.window = params["window"]
    if params.get("view") in VIEWS:
        state.view = params["view"]
    lat = _float_in(params.get("lat"), -90, 90)
    lon = _float_in(params.get("lon"), -180, 180)
    if lat is not None and lon is not None:
        state.near_lat, state.near_lon = lat, lon
    radius = _float_in(params.get("radius"), 1, 20_000)
    if radius is not None and int(radius) in RADII_KM:
        state.radius_km = int(radius)
    return state


def to_query(state: ViewState) -> dict[str, str]:
    """URL parameters for a state; defaults are left out to keep links short."""
    params: dict[str, str] = {}
    if state.region != "World":
        params["region"] = state.region
    if state.window != "24h":
        params["window"] = state.window
    if state.view != "map":
        params["view"] = state.view
    if state.near_active:
        params["lat"] = f"{state.near_lat:.4f}"
        params["lon"] = f"{state.near_lon:.4f}"
        params["radius"] = str(state.radius_km)
    return params


def share_url(base_url: str, state: ViewState) -> str:
    base = base_url.split("?")[0]
    query = urlencode(to_query(state))
    return f"{base}?{query}" if query else base
