"""Quick run: fetch today's world fire data from NASA and draw a map.

No database needed. Only needs NASA_FIRMS_MAP_KEY in .env.

    python scripts/quick_map.py            # last 24 hours, whole world
    python scripts/quick_map.py --days 2

Writes two files into ./output/:
    fire_map.html     open it in your browser
    fire_events.csv   one row per fire event
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import plotly.express as px

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.analysis.clustering import cluster_detections, summarize_events  # noqa: E402
from app.analysis.time_window import firms_day_range, keep_last_days  # noqa: E402
from app.ingestion.firms_client import FirmsClient  # noqa: E402
from app.ingestion.parser import parse_csv  # noqa: E402
from app.ingestion.products import SUPPORTED_PRODUCTS, get_product  # noqa: E402
from app.ingestion.validation import validate_records  # noqa: E402


def read_map_key() -> str:
    """Take the key from the environment, else from a simple KEY=value .env file."""
    key = os.getenv("NASA_FIRMS_MAP_KEY", "").strip()
    env_file = ROOT / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "NASA_FIRMS_MAP_KEY":
                key = value.strip().strip('"').strip("'")
    if not key or key == "your_map_key_here":
        sys.exit("NASA_FIRMS_MAP_KEY is missing. Put it in the .env file (see .env.example).")
    return key


def fetch_detections(client: FirmsClient, area: str, days: int) -> pd.DataFrame:
    frames = []
    for product in SUPPORTED_PRODUCTS:
        spec = get_product(product)
        result = client.fetch(product, area, firms_day_range(days))
        outcome = validate_records(parse_csv(result.csv_text, spec), spec)
        accepted, rejected = outcome.counts
        print(f"  {product:<18} {accepted:>7,} rows kept, {rejected:,} rejected")
        if outcome.accepted:
            frames.append(pd.DataFrame(outcome.accepted))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["acquired_at"] = pd.to_datetime(
        df["acq_date"].astype(str) + " " + df["acq_time_utc"].astype(str), utc=True
    )
    return keep_last_days(df, days)


def draw_map(events: pd.DataFrame, path: Path) -> None:
    # Sort so the most powerful fires are drawn last (on top of the small ones).
    plot = events.sort_values("max_frp_mw", na_position="first").copy()
    plot["first_seen"] = plot["first_seen"].dt.strftime("%Y-%m-%d %H:%M UTC")
    fig = px.scatter_geo(
        plot,
        lat="centroid_lat",
        lon="centroid_lon",
        size="n_detections",
        color="max_frp_mw",
        color_continuous_scale=["#f4a47a", "#eb6834", "#c94f1f", "#9a3512", "#6b2209"],
        range_color=(0, max(float(plot["max_frp_mw"].quantile(0.95)), 1.0)),
        opacity=0.85,
        hover_data={
            "event_id": True, "n_detections": True, "max_frp_mw": ":.1f",
            "total_frp_mw": ":.1f", "first_seen": True, "centroid_lat": ":.3f",
            "centroid_lon": ":.3f",
        },
        labels={"max_frp_mw": "Max FRP (MW)", "n_detections": "Pixels"},
        projection="natural earth",
        title=(
            f"Wildfire Sentinel: {len(events):,} fire events (NASA FIRMS VIIRS), "
            f"updated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC"
        ),
        size_max=12,
    )
    fig.update_geos(showcountries=True, countrycolor="#999", landcolor="#f2efe9")
    fig.update_layout(margin={"l": 0, "r": 0, "t": 50, "b": 0})
    fig.write_html(path, include_plotlyjs="cdn")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch NASA fire data and draw a world map.")
    parser.add_argument(
        "--days", type=int, default=1, choices=range(1, 10), metavar="1-9",
        help="rolling window in 24-hour days (default: last 24 hours)",
    )
    parser.add_argument(
        "--area", default="world", help="'world' or 'lon_min,lat_min,lon_max,lat_max'"
    )
    args = parser.parse_args()

    print(f"Fetching NASA FIRMS data (area={args.area}, days={args.days}) ...")
    detections = fetch_detections(FirmsClient(read_map_key()), args.area, args.days)
    if detections.empty:
        print("NASA returned no fire detections for this window.")
        return 0

    print("Grouping pixels into fire events ...")
    events = summarize_events(cluster_detections(detections))

    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    events.to_csv(out / "fire_events.csv", index=False)
    draw_map(events, out / "fire_map.html")

    print(f"\nDone: {len(detections):,} detections -> {len(events):,} fire events")
    print("Top 5 by total fire power (FRP):")
    top = events.nlargest(5, "total_frp_mw")[
        ["centroid_lat", "centroid_lon", "n_detections", "total_frp_mw"]
    ]
    print(top.round(2).to_string(index=False))
    print(f"\nOpen this file in your browser: {out / 'fire_map.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
