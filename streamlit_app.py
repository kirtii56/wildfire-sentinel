"""Wildfire Sentinel dashboard (Streamlit).

Run locally:   streamlit run streamlit_app.py
Needs NASA_FIRMS_MAP_KEY in .env, in the environment, or in Streamlit secrets.

Flow: fetch live NASA FIRMS data -> validate -> filter (region / near a point)
      -> group pixels into fire events (DBSCAN) -> map or globe, ranking, trends.
No database is needed; the data is fetched live and cached for 3 hours.

Performance notes: the heavy work (download, filtering, clustering) is cached per
view settings, so clicks and scrolling only redraw the page. The map draws the
strongest fires only (MAX_MAP_EVENTS); every event stays in the numbers, table
and downloads.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.analysis.clustering import cluster_detections, summarize_events
from app.analysis.time_window import firms_day_range, keep_last_days
from app.dashboard.export import events_to_geojson
from app.dashboard.geo import (
    REGIONS,
    circle_points,
    format_latlon,
    haversine_km,
    in_region,
    label_regions,
)
from app.dashboard.share import RADII_KM, WINDOWS, ViewState, from_query, share_url, to_query
from app.dashboard.trends import format_delta, pct_change, split_periods
from app.ingestion.firms_client import FirmsClient, FirmsError
from app.ingestion.parser import parse_csv
from app.ingestion.products import SUPPORTED_PRODUCTS, get_product
from app.ingestion.validation import validate_records

MAX_MAP_EVENTS = 2_500
KEEP_COLUMNS = ["latitude", "longitude", "acquired_at", "frp_mw", "scan_km", "track_km"]
WINDOW_LABELS = {"24h": "Last 24 h", "48h": "Last 48 h", "72h": "Last 72 h"}
VIEW_LABELS = {"map": "Flat map", "globe": "3D globe"}
CACHE_TTL = 3 * 60 * 60


@dataclass(frozen=True)
class Tokens:
    """Colours for one theme. Fire ramps are one orange hue, validated per surface."""

    ink: str
    ink_2: str
    muted: str
    surface: str
    grid: str
    baseline: str
    land: str
    ocean: str
    accent: str
    fire_ramp: tuple[str, ...]


DARK = Tokens(
    ink="#ffffff",
    ink_2="#c3c2b7",
    muted="#898781",
    surface="#1a1a19",
    grid="#2c2c2a",
    baseline="#383835",
    land="#242422",
    ocean="#111110",
    accent="#eb6834",
    fire_ramp=("#9a3a17", "#c94f1f", "#eb6834", "#f28b5e", "#f7b08c", "#fcd3b8"),
)
LIGHT = Tokens(
    ink="#0b0b0b",
    ink_2="#52514e",
    muted="#898781",
    surface="#fcfcfb",
    grid="#e1e0d9",
    baseline="#c3c2b7",
    land="#f0efec",
    ocean="#e3eaf0",
    accent="#d95926",
    fire_ramp=("#f28b5e", "#eb6834", "#c94f1f", "#9a3a17", "#6b2209"),
)

st.set_page_config(page_title="Wildfire Sentinel", page_icon="🔥", layout="wide")

theme_type = getattr(st.context.theme, "type", None) or "dark"
T = DARK if theme_type == "dark" else LIGHT

st.markdown(
    f"""
    <style>
      [data-testid="stHeader"] {{background: transparent;}}
      .block-container {{padding-top: 3.2rem; padding-bottom: 3rem; max-width: 1320px;}}
      .ws-eyebrow {{display:flex; align-items:center; gap:.5rem; color:{T.muted};
                    font-size:.78rem; letter-spacing:.08em; text-transform:uppercase;}}
      .ws-dot {{width:8px; height:8px; border-radius:50%; background:{T.accent};
                animation:ws-pulse 2s infinite;}}
      @keyframes ws-pulse {{
        0% {{box-shadow:0 0 0 0 rgba(235,104,52,.55);}}
        70% {{box-shadow:0 0 0 8px rgba(235,104,52,0);}}
        100% {{box-shadow:0 0 0 0 rgba(235,104,52,0);}}
      }}
      .ws-title {{font-size:2.5rem; font-weight:700; line-height:1.1; margin:.35rem 0 .35rem;}}
      .ws-sub {{color:{T.ink_2}; font-size:1.02rem; max-width:780px; margin-bottom:1.1rem;}}
      .ws-section {{font-size:1.05rem; font-weight:600; margin:1.4rem 0 .15rem;}}
      .ws-panel-title {{font-size:1.1rem; font-weight:650; line-height:1.3; margin-bottom:.15rem;}}
      .ws-note {{color:{T.muted}; font-size:.84rem;}}
      .ws-kv {{color:{T.muted}; font-size:.74rem; text-transform:uppercase; letter-spacing:.05em;}}
      .ws-v {{font-size:1.02rem; margin-bottom:.6rem;}}
      [data-testid="stMetricValue"] {{font-size:1.9rem;}}
      [data-testid="stMetricLabel"] p {{color:{T.ink_2}; font-size:.84rem;}}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------- Data ----------
def get_map_key() -> str:
    """Read the NASA key from the environment, a local .env file, or Streamlit secrets."""
    key = os.getenv("NASA_FIRMS_MAP_KEY", "").strip()
    env_file = Path(__file__).parent / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "NASA_FIRMS_MAP_KEY":
                key = value.strip().strip('"').strip("'")
    if not key:
        try:
            key = str(st.secrets.get("NASA_FIRMS_MAP_KEY", "")).strip()
        except Exception:  # no secrets file at all
            key = ""
    return "" if key == "your_map_key_here" else key


@st.cache_resource(ttl=CACHE_TTL, show_spinner="Fetching live NASA satellite data...")
def load_world_detections(days: int) -> tuple[pd.DataFrame, str]:
    """(detections, fetched_at): validated VIIRS detections, whole world, last ``days`` x 24 h.

    Shared between visitors and reruns (never modified), so it is not copied on each rerun.
    ``fetched_at`` keys the per-view cache, so views refresh exactly when the data does.
    """
    fetched_at = pd.Timestamp.now(tz="UTC").isoformat()
    client = FirmsClient(get_map_key())
    frames = []
    for product in SUPPORTED_PRODUCTS:
        spec = get_product(product)
        result = client.fetch(product, "world", firms_day_range(days))
        outcome = validate_records(parse_csv(result.csv_text, spec), spec)
        if outcome.accepted:
            frames.append(pd.DataFrame(outcome.accepted))
    if not frames:
        return pd.DataFrame(columns=KEEP_COLUMNS), fetched_at
    df = pd.concat(frames, ignore_index=True)
    df["acquired_at"] = pd.to_datetime(
        df["acq_date"].astype(str) + " " + df["acq_time_utc"].astype(str), utc=True
    )
    return keep_last_days(df[KEEP_COLUMNS], days), fetched_at


def _filter(df: pd.DataFrame, region: str, near: tuple[float, float, int] | None):
    if df.empty:
        return df
    df = df[in_region(df["latitude"], df["longitude"], region)]
    if near is not None:
        lat, lon, radius = near
        df = df[haversine_km(lat, lon, df["latitude"], df["longitude"]) <= radius]
    return df


@st.cache_data(ttl=CACHE_TTL, show_spinner="Grouping pixels into fire events...")
def view_data(
    days: int,
    region: str,
    near: tuple[float, float, int] | None,
    eps_km: float,
    max_gap_hours: float,
    fetched_at: str,
) -> dict:
    """Everything the page needs for one set of view settings (cached, so clicks are fast).

    Keyed on small arguments only. Windows are measured back from ``fetched_at``, the
    moment the NASA data was downloaded, so the result is stable until the next download.
    """
    raw, _ = load_world_detections(2 * days)  # this window + the one before, for trends
    current, previous = split_periods(raw, days, pd.Timestamp(fetched_at))
    current, previous = _filter(current, region, near), _filter(previous, region, near)
    result = {
        "n_detections": len(current),
        "power_now": float(current["frp_mw"].sum()),
        "power_before": float(previous["frp_mw"].sum()) if len(previous) else float("nan"),
        "latest_pass": current["acquired_at"].max() if len(current) else None,
        "bins_now": _bins(current),
        "bins_before": _bins(previous),
        "events": None,
    }
    if current.empty:
        return result
    clustered = cluster_detections(current, eps_km=eps_km, max_gap_hours=max_gap_hours)
    events = summarize_events(clustered)
    events["region"] = label_regions(events["centroid_lat"], events["centroid_lon"])
    events["location"] = [
        format_latlon(lat, lon)
        for lat, lon in zip(events["centroid_lat"], events["centroid_lon"], strict=True)
    ]
    if near is not None:
        events["distance_km"] = haversine_km(
            near[0], near[1], events["centroid_lat"], events["centroid_lon"]
        )
    result["events"] = events
    return result


def _bins(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype="int64")
    return df.set_index("acquired_at").resample("3h").size()


def pixels(n) -> str:
    n = int(n)
    return f"{n:,} pixel" if n == 1 else f"{n:,} pixels"


# ---------- State: URL <-> widgets ----------
if "initialised" not in st.session_state:
    start = from_query(st.query_params.to_dict())
    st.session_state.update(
        initialised=True,
        region=start.region,
        window=WINDOW_LABELS[start.window],
        view=VIEW_LABELS[start.view],
        near_on=start.near_active,
        near_lat=start.near_lat if start.near_active else -11.42,
        near_lon=start.near_lon if start.near_active else -43.45,
        radius=start.radius_km,
    )

# ---------- Header ----------
st.markdown(
    """
    <div class="ws-eyebrow"><span class="ws-dot"></span>Live · NASA FIRMS satellite data</div>
    <div class="ws-title">Wildfire Sentinel</div>
    <div class="ws-sub">Every active fire seen from space. Satellites report hot pixels;
    DBSCAN clustering groups neighbouring pixels into fire events. Click a fire for details,
    select an area with the map's box tool, or search near a place.</div>
    """,
    unsafe_allow_html=True,
)

if not get_map_key():
    st.error("NASA_FIRMS_MAP_KEY is missing. Add it to .env or to the app's Streamlit secrets.")
    st.stop()

# ---------- Controls (one row, above the content) ----------
cols = st.columns([1.25, 1.55, 1.2, 1.0, 0.9, 0.8], vertical_alignment="bottom")
cols[0].selectbox("Region", list(REGIONS), key="region")
cols[1].segmented_control("Time window", list(WINDOW_LABELS.values()), key="window")
cols[2].segmented_control("View", list(VIEW_LABELS.values()), key="view")
with cols[3].popover("📍 Near a place", width="stretch"):
    st.toggle("Only show fires near a point", key="near_on")
    a, b = st.columns(2)
    a.number_input("Latitude", -90.0, 90.0, step=0.1, format="%.4f", key="near_lat")
    b.number_input("Longitude", -180.0, 180.0, step=0.1, format="%.4f", key="near_lon")
    st.select_slider("Radius (km)", options=list(RADII_KM), key="radius")
    st.caption("Tip: in Google Maps, right-click a place to copy its coordinates.")
with cols[4].popover("⚙ Options", width="stretch"):
    min_pixels = st.select_slider(
        "Minimum fire size (pixels)", options=[1, 2, 5, 10, 20, 50], value=1
    )
    eps_km = st.slider("Clustering: max distance between pixels (km)", 0.5, 5.0, 1.0, 0.5)
    max_gap_hours = st.slider("Clustering: max time gap in one fire (h)", 6, 72, 24, 6)
    st.caption("Light / dark mode: ⋮ menu (top right) → Settings.")

window_key = next((k for k, v in WINDOW_LABELS.items() if v == st.session_state.window), "24h")
view_key = next((k for k, v in VIEW_LABELS.items() if v == st.session_state.view), "map")
state = ViewState(
    region=st.session_state.region,
    window=window_key,
    view=view_key,
    near_lat=st.session_state.near_lat if st.session_state.near_on else None,
    near_lon=st.session_state.near_lon if st.session_state.near_on else None,
    radius_km=st.session_state.radius,
)
st.query_params.from_dict(to_query(state))
with cols[5].popover("🔗 Share", width="stretch"):
    st.caption("Link to exactly this view:")
    st.code(
        share_url(st.context.url or "https://wildfire-sentinel.streamlit.app/", state),
        language=None,
    )

days = WINDOWS[state.window]
period = f"{days * 24} h"
near = (state.near_lat, state.near_lon, state.radius_km) if state.near_active else None

# ---------- Data for this view (cached) ----------
try:
    _, fetched_at = load_world_detections(2 * days)
    data = view_data(days, state.region, near, float(eps_km), float(max_gap_hours), fetched_at)
except FirmsError as exc:
    st.error(f"Could not fetch data from NASA FIRMS. Try again in a few minutes.\n\n{exc}")
    st.stop()

if data["events"] is None:
    where = f"within {state.radius_km} km of that point" if near else "here"
    st.info(f"No fire detections {where} in the last {period}. Try a wider area or window.")
    st.stop()

events = data["events"]
shown = events[events["n_detections"] >= min_pixels]
if shown.empty:
    st.info("No fire events this large here. Lower the minimum size in ⚙ Options.")
    st.stop()
ranked = shown.dropna(subset=["total_frp_mw"])
by_id = shown.set_index("event_id")


# ---------- Selection (read before drawing, so the map can highlight it) ----------
def selected_ids_from_map() -> list[int]:
    sel = st.session_state.get("fire_map")
    points = sel.selection.points if sel and sel.selection else []
    ids = [int(p["customdata"][0]) for p in points if p.get("customdata")]
    return [i for i in ids if i in by_id.index]


def selected_id_from_table() -> int | None:
    sel = st.session_state.get("top_table")
    rows = sel.selection.rows if sel and sel.selection else []
    top_ids = st.session_state.get("top_ids", [])
    if rows and rows[0] < len(top_ids) and top_ids[rows[0]] in by_id.index:
        return top_ids[rows[0]]
    return None


map_ids = selected_ids_from_map()
focus_id = map_ids[0] if len(map_ids) == 1 else selected_id_from_table()

# ---------- Headline numbers ----------
largest = shown.loc[shown["n_detections"].idxmax()]
strongest = ranked.loc[ranked["total_frp_mw"].idxmax()] if len(ranked) else None

k1, k2, k3, k4 = st.columns(4)
with k1.container(border=True, height=172):
    st.metric("Fire events", f"{len(shown):,}")
    st.caption(f"from {data['n_detections']:,} satellite detections")
with k2.container(border=True, height=172):
    st.metric(
        "Total fire power",
        f"{data['power_now']:,.0f} MW",
        delta=format_delta(pct_change(data["power_now"], data["power_before"]), period),
        delta_color="inverse",
    )
    st.caption("all fires combined")
with k3.container(border=True, height=172):
    if strongest is None:
        st.metric("Most intense fire", "n/a")
        st.caption("no fire power reported")
    else:
        st.metric("Most intense fire", f"{strongest['total_frp_mw']:,.0f} MW")
        st.caption(f"{strongest['location']} · {pixels(strongest['n_detections'])}")
with k4.container(border=True, height=172):
    if near:
        nearest = shown.loc[shown["distance_km"].idxmin()]
        st.metric("Nearest fire", f"{nearest['distance_km']:,.0f} km")
        st.caption(f"{nearest['location']} · {len(shown):,} fires within {state.radius_km} km")
    elif state.region == "World" and len(ranked):
        power = ranked.groupby("region")["total_frp_mw"].sum()
        st.metric("Most fire power", power.idxmax())
        st.caption(f"{power.max() / power.sum():.0%} of all fire power worldwide")
    else:
        st.metric("Largest fire", pixels(largest["n_detections"]))
        st.caption(largest["location"])


# ---------- Map / globe ----------
def map_center() -> tuple[float, float]:
    if focus_id is not None:
        row = by_id.loc[focus_id]
        return float(row["centroid_lat"]), float(row["centroid_lon"])
    if near:
        return near[0], near[1]
    bbox = REGIONS[state.region]
    if bbox is not None:
        return (bbox[1] + bbox[3]) / 2, (bbox[0] + bbox[2]) / 2
    return 5.0, 20.0


def build_map(plot: pd.DataFrame) -> go.Figure:
    # Stretch the colour scale over the fires actually drawn (whole powers of ten).
    color_min = float(np.floor(plot["log_frp"].min())) if len(plot) else 0.0
    color_max = float(np.ceil(plot["log_frp"].max())) if len(plot) else 3.0
    color_max = max(color_max, color_min + 1)
    ticks = list(range(int(color_min), int(color_max) + 1))
    custom = plot[
        [
            "event_id",
            "location",
            "region",
            "total_frp_mw",
            "n_detections",
            "duration_hours",
            "first_seen_utc",
        ]
    ].to_numpy()
    fig = go.Figure(
        go.Scattergeo(
            lat=plot["centroid_lat"],
            lon=plot["centroid_lon"],
            customdata=custom,
            mode="markers",
            marker={
                "size": plot["marker_size"],
                "color": plot["log_frp"],
                "colorscale": list(T.fire_ramp),
                "cmin": color_min,
                "cmax": color_max,
                "opacity": 0.9,
                "line": {"width": 0.6, "color": T.surface},
                "colorbar": {
                    "title": {"text": "Fire power (MW)", "side": "top"},
                    "orientation": "h",
                    "x": 0,
                    "xanchor": "left",
                    "y": -0.02,
                    "yanchor": "top",
                    "len": 0.4,
                    "thickness": 8,
                    "outlinewidth": 0,
                    "tickvals": ticks,
                    "ticktext": [f"{10**t:,}" for t in ticks],
                    "tickfont": {"color": T.muted, "size": 11},
                },
            },
            # Keep every fire fully visible after a click (Plotly fades unselected points).
            selected={"marker": {"opacity": 1}},
            unselected={"marker": {"opacity": 0.9}},
            hovertemplate=(
                "<b>%{customdata[1]}</b> · %{customdata[2]}<br>"
                "Fire power: %{customdata[3]:,.0f} MW<br>"
                "Satellite pixels: %{customdata[4]:,}<br>"
                "Burning: %{customdata[5]:.1f} h<extra></extra>"
            ),
            name="Fires",
        )
    )
    if near:
        c_lat, c_lon = circle_points(near[0], near[1], near[2])
        fig.add_trace(
            go.Scattergeo(
                lat=c_lat,
                lon=c_lon,
                mode="lines",
                hoverinfo="skip",
                line={"color": T.ink_2, "width": 1.5, "dash": "dot"},
            )
        )
        fig.add_trace(
            go.Scattergeo(
                lat=[near[0]],
                lon=[near[1]],
                mode="markers",
                marker={"size": 9, "symbol": "x", "color": T.ink},
                hovertemplate="Your point<extra></extra>",
            )
        )
    if focus_id is not None:
        row = by_id.loc[focus_id]
        fig.add_trace(
            go.Scattergeo(
                lat=[row["centroid_lat"]],
                lon=[row["centroid_lon"]],
                mode="markers",
                hoverinfo="skip",
                marker={
                    "size": 24,
                    "color": "rgba(0,0,0,0)",
                    "line": {"width": 2.5, "color": T.ink},
                },
            )
        )
    lat0, lon0 = map_center()
    globe = state.view == "globe"
    zoom_in = not globe and (state.region != "World" or near is not None)
    fig.update_geos(
        projection_type="orthographic" if globe else "natural earth",
        bgcolor="rgba(0,0,0,0)",
        showframe=False,
        showocean=True,
        oceancolor=T.ocean,
        showland=True,
        landcolor=T.land,
        showcountries=True,
        countrycolor=T.baseline,
        countrywidth=0.5,
        showcoastlines=True,
        coastlinecolor=T.baseline,
        coastlinewidth=0.5,
        showlakes=False,
        fitbounds="locations" if zoom_in else False,
    )
    if globe:
        fig.update_geos(projection_rotation={"lon": lon0, "lat": lat0})
    elif not zoom_in:
        fig.update_geos(lataxis_range=[-58, 84])
    fig.update_layout(
        height=540,
        margin={"l": 0, "r": 0, "t": 4, "b": 0},
        paper_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        dragmode="pan",  # click = fire details; box/lasso select lives in the toolbar
        font={"color": T.ink_2},
        hoverlabel={
            "bgcolor": T.surface,
            "bordercolor": T.baseline,
            "font": {"color": T.ink, "size": 13},
        },
    )
    return fig


def fact_grid(facts: list[tuple[str, str]]) -> None:
    for i in range(0, len(facts), 2):
        left, right = st.columns(2)
        for col, (label, value) in zip((left, right), facts[i : i + 2], strict=False):
            col.markdown(
                f'<div class="ws-kv">{label}</div><div class="ws-v">{value}</div>',
                unsafe_allow_html=True,
            )


def fire_panel(row: pd.Series, heading: str) -> None:
    lat, lon = float(row["centroid_lat"]), float(row["centroid_lon"])
    st.markdown(f'<div class="ws-kv">{heading}</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="ws-panel-title">🔥 {row["location"]}</div>'
        f'<div class="ws-note" style="margin-bottom:.8rem">{row["region"]}</div>',
        unsafe_allow_html=True,
    )
    facts = [
        ("Fire power", f"{row['total_frp_mw']:,.0f} MW"),
        ("Peak pixel", f"{row['max_frp_mw']:,.0f} MW"),
        ("Size", pixels(row["n_detections"])),
        ("Burning for", f"{row['duration_hours']:.1f} h"),
        ("First seen", f"{row['first_seen']:%d %b %H:%M} UTC"),
        ("Last seen", f"{row['last_seen']:%d %b %H:%M} UTC"),
    ]
    if "distance_km" in row:
        facts.append(("From your point", f"{row['distance_km']:,.0f} km"))
    fact_grid(facts)
    st.code(f"{lat:.5f}, {lon:.5f}", language=None)
    st.link_button(
        "Open in Google Maps",
        f"https://www.google.com/maps?q={lat:.5f},{lon:.5f}",
        width="stretch",
    )
    st.link_button(
        "Open in NASA FIRMS",
        f"https://firms.modaps.eosdis.nasa.gov/map/#d:24hrs;@{lon:.4f},{lat:.4f},10.0z",
        width="stretch",
    )


plot = ranked.nlargest(MAX_MAP_EVENTS, "total_frp_mw").sort_values("total_frp_mw").copy()
# Fire power spans ~1 to 20,000+ MW, so colour uses a log scale. Size grows with the log
# of the pixel count, so large fires stand out without covering their neighbours.
plot["log_frp"] = np.log10(plot["total_frp_mw"].clip(lower=1.0))
plot["marker_size"] = 4 + 3 * np.log10(plot["n_detections"])
plot["first_seen_utc"] = plot["first_seen"].dt.strftime("%d %b %H:%M UTC")

map_col, panel_col = st.columns([2.3, 1], gap="medium")
with map_col:
    section = "Fires on the globe" if state.view == "globe" else "Where the fires are"
    st.markdown(f'<div class="ws-section">{section}</div>', unsafe_allow_html=True)
    st.plotly_chart(
        build_map(plot),
        key="fire_map",
        on_select="rerun",
        selection_mode=("points", "box", "lasso"),
        theme=None,
        config={"displaylogo": False, "scrollZoom": False},
    )
    drawn = (
        f"Showing the {len(plot):,} most intense of {len(ranked):,} fires; "
        "all of them are in the table and downloads. "
        if len(plot) < len(ranked)
        else ""
    )
    how = (
        "Drag to rotate the globe."
        if state.view == "globe"
        else "Drag to pan; for an area, pick the box tool (top right of the map) and drag."
    )
    st.markdown(
        f'<div class="ws-note">{drawn}Size = satellite pixels, colour = fire power (log scale). '
        f"{how} Double-click the map to clear a selection.</div>",
        unsafe_allow_html=True,
    )

with panel_col:
    st.markdown('<div class="ws-section">&nbsp;</div>', unsafe_allow_html=True)
    with st.container(border=True, height=560):
        if len(map_ids) > 1:
            sel = shown[shown["event_id"].isin(map_ids)]
            st.markdown('<div class="ws-kv">Your selection</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div class="ws-panel-title">{len(sel):,} fires selected</div>',
                unsafe_allow_html=True,
            )
            fact_grid(
                [
                    ("Fire power", f"{sel['total_frp_mw'].sum():,.0f} MW"),
                    ("Satellite pixels", f"{int(sel['n_detections'].sum()):,}"),
                    ("Strongest fire", f"{sel['total_frp_mw'].max():,.0f} MW"),
                    ("Longest burning", f"{sel['duration_hours'].max():.1f} h"),
                ]
            )
            st.download_button(
                "Download selection (CSV)",
                lambda sel=sel: sel.drop(columns=["location"]).to_csv(index=False),
                file_name="fire_events_selection.csv",
                mime="text/csv",
                width="stretch",
            )
            st.download_button(
                "Download selection (GeoJSON)",
                lambda sel=sel: events_to_geojson(sel),
                file_name="fire_events_selection.geojson",
                mime="application/geo+json",
                width="stretch",
            )
            st.caption("Double-click the map to clear the selection.")
        elif focus_id is not None:
            fire_panel(by_id.loc[focus_id], "Selected fire")
        elif strongest is not None:
            fire_panel(strongest, "Most intense fire right now")
            st.caption("👆 Click any fire on the map to see its details here.")

# ---------- Ranking + activity ----------
left, right = st.columns([1.4, 1], gap="large")

with left:
    title = "Nearest fires" if near else "Most intense fires"
    st.markdown(f'<div class="ws-section">{title}</div>', unsafe_allow_html=True)
    if near:
        top = shown.nsmallest(10, "distance_km").reset_index(drop=True)
    else:
        top = ranked.nlargest(10, "total_frp_mw").reset_index(drop=True)
    st.session_state["top_ids"] = [int(i) for i in top["event_id"]]
    top.insert(0, "rank", np.arange(1, len(top) + 1))
    columns = ["rank", "location", "region", "total_frp_mw", "n_detections", "duration_hours"]
    if near:  # everything is in one area, so distance replaces region
        columns[columns.index("region")] = "distance_km"
    bar_max = float(top["total_frp_mw"].max()) if top["total_frp_mw"].notna().any() else 1.0
    st.dataframe(
        top[columns],
        key="top_table",
        on_select="rerun",
        selection_mode="single-row",
        hide_index=True,
        width="stretch",
        column_config={
            "rank": st.column_config.NumberColumn("#", width="small"),
            "location": "Location",
            "distance_km": st.column_config.NumberColumn("Distance (km)", format="%.0f"),
            "region": "Region",
            "total_frp_mw": st.column_config.ProgressColumn(
                "Fire power (MW)", format="%.0f", min_value=0, max_value=bar_max
            ),
            "n_detections": st.column_config.NumberColumn("Pixels", format="%d"),
            "duration_hours": st.column_config.NumberColumn("Burning (h)", format="%.1f"),
        },
    )
    st.caption("Select a row to show that fire on the map and in the details panel.")
    e1, e2 = st.columns(2)
    e1.download_button(
        f"All {len(shown):,} fires (CSV)",
        lambda: shown.drop(columns=["location"]).to_csv(index=False),
        file_name="fire_events.csv",
        mime="text/csv",
        width="stretch",
    )
    e2.download_button(
        f"All {len(shown):,} fires (GeoJSON)",
        lambda: events_to_geojson(shown),
        file_name="fire_events.geojson",
        mime="application/geo+json",
        width="stretch",
    )

with right:
    st.markdown('<div class="ws-section">Detections over time</div>', unsafe_allow_html=True)
    bar = go.Figure()
    for bins, color, label in (
        (data["bins_before"], T.baseline, f"previous {period}"),
        (data["bins_now"], T.accent, f"last {period}"),
    ):
        if bins.empty:
            continue
        bar.add_trace(
            go.Bar(
                x=bins.index,
                y=bins.to_numpy(),
                name=label,
                marker={"color": color, "cornerradius": 3},
                hovertemplate="%{x|%d %b %H:%M} UTC<br>%{y:,} detections<extra>"
                + label
                + "</extra>",
            )
        )
    bar.update_layout(
        height=330,
        margin={"l": 44, "r": 0, "t": 8, "b": 0},
        bargap=0.2,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": T.ink_2},
        legend={"orientation": "h", "y": 1.1, "x": 0, "font": {"color": T.ink_2}},
        hoverlabel={
            "bgcolor": T.surface,
            "bordercolor": T.baseline,
            "font": {"color": T.ink, "size": 13},
        },
    )
    bar.update_xaxes(
        showgrid=False,
        linecolor=T.baseline,
        tickformat="%d %b<br>%H:%M",
        tickfont={"color": T.muted},
    )
    bar.update_yaxes(
        gridcolor=T.grid,
        zeroline=False,
        tickformat="~s",
        tickfont={"color": T.muted},
        automargin=True,
        title=None,
    )
    st.plotly_chart(bar, theme=None, config={"displaylogo": False})
    st.markdown(
        '<div class="ws-note">3-hour bins, UTC. Each satellite passes over a region about '
        "twice a day, so gaps mean no satellite overhead, not no fires.</div>",
        unsafe_allow_html=True,
    )

# ---------- Data guide + footer ----------
with st.expander("About the data (what each column means)"):
    st.markdown(
        """
| Column | Meaning |
|---|---|
| `event_id` | Fire event number in this view (changes when filters change) |
| `centroid_lat`, `centroid_lon` | Centre of the fire event (degrees) |
| `n_detections` | Satellite pixels (375 m VIIRS pixels) grouped into this event |
| `total_frp_mw` / `max_frp_mw` | Fire radiative power: sum over pixels / strongest pixel (MW) |
| `duration_hours` | Time between the first and last satellite detection |
| `pixel_footprint_km2` | Summed pixel area, **not** burned area |
| `first_seen`, `last_seen` | UTC timestamps of the first and last detection |
| `region` | Approximate continent (bounding boxes, not borders) |
| `distance_km` | Distance from your point (only with "Near a place") |

Fire events are estimates from DBSCAN clustering (default: pixels within 1 km, gaps up to
24 h), not official fire perimeters. Source: NASA FIRMS near-real-time VIIRS data.
        """
    )

st.divider()
latest = data["latest_pass"]
latest_text = f"{latest:%d %b %H:%M} UTC" if latest is not None else "n/a"
st.markdown(
    f"""
    <div class="ws-note">
    Data: NASA FIRMS, VIIRS on NOAA-20, NOAA-21 and Suomi NPP, refreshed every 3 hours.
    Latest satellite pass in view: {latest_text}.
    Source code: <a href="https://github.com/kirtii56/wildfire." style="color:{T.ink_2}">GitHub</a>.
    </div>
    """,
    unsafe_allow_html=True,
)
