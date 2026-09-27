"""Group stored detections into fire events and print a summary.

Example:
    python scripts/cluster_fires.py --days 1
    python scripts/cluster_fires.py --days 3 --csv events.csv
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis.clustering import cluster_detections, summarize_events  # noqa: E402
from app.analysis.queries import load_detections  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.database.connection import connect  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=1, help="look back this many days")
    parser.add_argument("--eps-km", type=float, default=1.0)
    parser.add_argument("--max-gap-hours", type=float, default=24.0)
    parser.add_argument("--csv", type=Path, help="also write the event table to this CSV")
    args = parser.parse_args()

    end = datetime.now(UTC)
    start = end - timedelta(days=args.days)

    with connect(get_settings().database_url) as conn:
        detections = load_detections(conn, start, end)

    if detections.empty:
        print("No detections in that window. Run scripts/run_ingest.py first.")
        return 0

    events = summarize_events(
        cluster_detections(detections, eps_km=args.eps_km, max_gap_hours=args.max_gap_hours)
    )
    print(f"{len(detections):,} detections -> {len(events):,} fire events")
    print("\nLargest 10 events by total FRP:")
    print(events.sort_values("total_frp_mw", ascending=False).head(10).to_string(index=False))

    if args.csv:
        events.to_csv(args.csv, index=False)
        print(f"\nWrote {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
