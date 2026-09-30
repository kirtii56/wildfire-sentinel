"""Wildfire Sentinel dashboard (Streamlit).

Run locally:   streamlit run streamlit_app.py
Needs NASA_FIRMS_MAP_KEY in .env, in the environment, or in Streamlit secrets.

Flow: fetch live NASA FIRMS data -> validate -> filter to a region
      -> group pixels into fire events (DBSCAN) -> show map, table and chart.
No database is needed; the data is fetched live and cached for 3 hours.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from app.analysis.clustering import cluster_detections, summarize_events
from app.analysis.time_window import firms_day_range, keep_last_days
from app.ingestion.firms_client import FirmsClient, FirmsError
from app.ingestion.parser import parse_csv
from app.ingestion.products import SUPPORTED_PRODUCTS, get_product
from app.ingestion.validation import validate_records

# Regions as bounding boxes: (lon_min, lat_min, lon_max, lat_max)
REGIONS: dict[str, tuple[float, float, float, float] | None] = {
    "World": None,
    "Africa": (-20.0, -36.0, 55.0, 38.0),
    "Asia": (25.0, -11.0, 180.0, 82.0),
    "Australia & Oceania": (110.0, -50.0, 180.0, 0.0),
    "Europe": (-25.0, 34.0, 45.0, 72.0),
    "North America": (-170.0, 7.0, -50.0, 84.0),
    "South America": (-92.0, -56.0, -30.0, 13.0),
}

TIME_FMT = "D MMM, HH:mm"

# One orange hue, light (small fire) -> dark (powerful fire).
FIRE_SCALE = ["#f4a47a", "#eb6834", "#c94f1f", "#9a3512", "#6b2209"]

st.set_page_config(page_title="Wildfire Sentinel", page_icon="🔥", layout="wide")


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


@st.cache_data(ttl=3 * 60 * 60, show_spinner="Fetching live NASA satellite data...")
def load_world_detections(days: int) -> pd.DataFrame:
    """Validated VIIRS detections for the whole world from the last ``days`` x 24 hours."""
    client = FirmsClient(get_map_key())
    frames = []
    for product in SUPPORTED_PRODUCTS:
        spec = get_product(product)
        result = client.fetch(product, "world", firms_day_range(days))
        outcome = validate_records(parse_csv(result.csv_text, spec), spec)
        if outcome.accepted:
            frames.append(pd.DataFrame(outcome.accepted))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["acquired_at"] = pd.to_datetime(
        df["acq_date"].astype(str) + " " + df["acq_time_utc"].astype(str), utc=True
    )
    return keep_last_days(df, days)


def filter_region(df: pd.DataFrame, region: str) -> pd.DataFrame:
    bbox = REGIONS[region]
    if bbox is None or df.empty:
        return df
    lon_min, lat_min, lon_max, lat_max = bbox
    inside = df["longitude"].between(lon_min, lon_max) & df["latitude"].between(lat_min, lat_max)
    return df[inside]


@st.cache_data(ttl=3 * 60 * 60, show_spinner="Grouping pixels into fire events...")
def build_events(detections: pd.DataFrame, eps_km: float, max_gap_hours: float) -> pd.DataFrame:
    clustered = cluster_detections(detections, eps_km=eps_km, max_gap_hours=max_gap_hours)
    return summarize_events(clustered)


# ---------- Sidebar: user choices ----------
with st.sidebar:
    st.header("Filters")
    region = st.selectbox("Region", list(REGIONS))
    days = st.radio("Time window", [1, 2, 3], format_func=lambda d: f"Last {d * 24} h",
                    horizontal=True)
    min_pixels = st.slider("Hide events smaller than (pixels)", 1, 20, 1)
    with st.expander("Clustering settings"):
        eps_km = st.slider("Max distance between pixels (km)", 0.5, 5.0, 1.0, 0.5)
        max_gap_hours = st.slider("Max time gap (hours)", 6, 72, 24, 6)
    st.caption(
        "Data: NASA FIRMS, VIIRS satellites (NOAA-20, NOAA-21, Suomi NPP). "
        "Refreshed every 3 hours."
    )

# ---------- Header ----------
st.title("🔥 Wildfire Sentinel")
st.caption(
    "Live satellite fire detections from NASA, grouped into fire events with DBSCAN "
    "clustering. Events are estimates, not official fire perimeters."
)

if not get_map_key():
    st.error("NASA_FIRMS_MAP_KEY is missing. Add it to .env or to the app's Streamlit secrets.")
    st.stop()

try:
    world = load_world_detections(days)
except FirmsError as exc:
    st.error(f"Could not fetch data from NASA FIRMS. Try again in a few minutes.\n\n{exc}")
    st.stop()

detections = filter_region(world, region)
if detections.empty:
    st.info("NASA reported no fire detections for this region and time window.")
    st.stop()

events = build_events(detections, eps_km, float(max_gap_hours))
shown = events[events["n_detections"] >= min_pixels]

# ---------- Headline numbers ----------
c1, c2, c3, c4 = st.columns(4)
c1.metric("Satellite detections", f"{len(detections):,}")
c2.metric("Fire events", f"{len(events):,}")
c3.metric("Largest event", f"{int(events['n_detections'].max()):,} pixels")
peak = events["total_frp_mw"].max()
c4.metric("Most powerful event", "n/a" if pd.isna(peak) else f"{peak:,.0f} MW")

map_tab, table_tab, activity_tab = st.tabs(["🗺️ Map", "🔥 Biggest fires", "📈 Activity"])

# ---------- Map ----------
with map_tab:
    # Keep the 20,000 most powerful events; sort so the biggest fires are drawn on top.
    plot = shown.dropna(subset=["total_frp_mw"]).nlargest(20_000, "total_frp_mw")
    plot = plot.sort_values("total_frp_mw").copy()
    plot["first_seen_utc"] = plot["first_seen"].dt.strftime("%d %b %H:%M UTC")
    color_max = max(float(plot["total_frp_mw"].quantile(0.95)), 1.0) if len(plot) else 1.0
    fig = px.scatter_map(
        plot,
        lat="centroid_lat",
        lon="centroid_lon",
        size="n_detections",
        size_max=14,
        opacity=0.85,
        color="total_frp_mw",
        color_continuous_scale=FIRE_SCALE,
        range_color=(0, color_max),
        hover_name=plot["event_id"].map(lambda i: f"Fire event #{i}"),
        hover_data={
            "n_detections": True, "total_frp_mw": ":,.1f", "max_frp_mw": ":,.1f",
            "duration_hours": ":.1f", "first_seen_utc": True,
            "centroid_lat": ":.3f", "centroid_lon": ":.3f",
        },
        labels={
            "n_detections": "Pixels", "total_frp_mw": "Total fire power (MW)",
            "max_frp_mw": "Peak pixel power (MW)", "duration_hours": "Burning for (h)",
            "first_seen_utc": "First seen", "centroid_lat": "Lat", "centroid_lon": "Lon",
        },
        zoom=1 if region == "World" else 2,
        height=620,
        map_style="carto-positron",
    )
    fig.update_layout(
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        coloraxis_colorbar={"title": "Fire power<br>(MW)", "thickness": 12},
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        f"Showing {len(plot):,} fire events. Bubble size = number of satellite pixels; "
        "colour = total fire radiative power (FRP). Hover a bubble for details."
    )

# ---------- Table ----------
with table_tab:
    top = shown.sort_values("total_frp_mw", ascending=False, na_position="last").head(25)
    st.dataframe(
        top[["event_id", "n_detections", "total_frp_mw", "max_frp_mw", "duration_hours",
             "centroid_lat", "centroid_lon", "first_seen", "last_seen"]],
        hide_index=True,
        use_container_width=True,
        column_config={
            "event_id": "Event",
            "n_detections": st.column_config.NumberColumn("Pixels", format="%d"),
            "total_frp_mw": st.column_config.NumberColumn("Total power (MW)", format="%.1f"),
            "max_frp_mw": st.column_config.NumberColumn("Peak pixel (MW)", format="%.1f"),
            "duration_hours": st.column_config.NumberColumn("Duration (h)", format="%.1f"),
            "centroid_lat": st.column_config.NumberColumn("Lat", format="%.3f"),
            "centroid_lon": st.column_config.NumberColumn("Lon", format="%.3f"),
            "first_seen": st.column_config.DatetimeColumn("First seen (UTC)", format=TIME_FMT),
            "last_seen": st.column_config.DatetimeColumn("Last seen (UTC)", format=TIME_FMT),
        },
    )
    st.download_button(
        "Download all fire events (CSV)",
        shown.to_csv(index=False).encode(),
        file_name=f"fire_events_{region.lower().replace(' ', '_')}.csv",
        mime="text/csv",
    )

# ---------- Activity over time ----------
with activity_tab:
    hourly = (
        detections.set_index("acquired_at")
        .resample("3h")
        .size()
        .rename("detections")
        .reset_index()
    )
    hourly["window"] = hourly["acquired_at"].dt.strftime("%d %b %H:%M")
    bar = px.bar(
        hourly, x="window", y="detections",
        labels={"window": "3-hour window (UTC)", "detections": "Detections"},
        color_discrete_sequence=["#eb6834"],
        height=380,
    )
    bar.update_traces(marker_line_width=0, hovertemplate="%{x}<br>%{y:,} detections<extra></extra>")
    bar.update_layout(margin={"l": 0, "r": 0, "t": 10, "b": 0}, bargap=0.25)
    bar.update_yaxes(gridcolor="rgba(128,128,128,0.2)", tickformat=",")
    st.subheader("Satellite detections per 3 hours")
    st.plotly_chart(bar, use_container_width=True)
    st.caption(
        "Detections arrive in bursts because each satellite passes over a region about "
        "twice a day, so gaps mean 'no satellite overhead', not 'no fires'."
    )
