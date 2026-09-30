"""The only production ingestion entry point.

Writes to the database exactly what NASA FIRMS returned, and nothing else.

Examples
--------
    python scripts/run_ingest.py --area 68,6,98,38 --day-range 1
    python scripts/run_ingest.py --area world --product VIIRS_NOAA20_NRT --day-range 2

--area is required (via the flag or FIRMS_AREA in .env) and has no default: a
world VIIRS query returns tens of thousands of rows per day and costs a
meaningful share of the FIRMS transaction quota, so the scope is always an
explicit choice.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.database.connection import connect  # noqa: E402
from app.ingestion.firms_client import FirmsClient  # noqa: E402
from app.ingestion.pipeline import ingest_product  # noqa: E402
from app.ingestion.products import SUPPORTED_PRODUCTS  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--product",
        action="append",
        choices=sorted(SUPPORTED_PRODUCTS),
        help="FIRMS product key. Repeatable. Defaults to FIRMS_PRODUCTS.",
    )
    parser.add_argument(
        "--area",
        help="'world' or 'lon_min,lat_min,lon_max,lat_max'. Defaults to FIRMS_AREA.",
    )
    parser.add_argument(
        "--day-range", type=int, help="Days to fetch, 1-5. Defaults to FIRMS_DAY_RANGE."
    )
    parser.add_argument("--date", help="Optional FIRMS start date, YYYY-MM-DD.")
    parser.add_argument("--json", action="store_true", help="Print the run summaries as JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    logger = configure_logging(settings.log_level)

    area = args.area or settings.firms_area
    if not area:
        logger.error(
            "No ingest area specified. Pass --area or set FIRMS_AREA in .env. "
            "There is no default because the scope determines both data volume "
            "and FIRMS transaction usage."
        )
        return 2

    products = args.product or settings.product_list
    day_range = args.day_range or settings.firms_day_range

    client = FirmsClient(map_key=settings.nasa_firms_map_key)
    summaries = []

    with connect(settings.database_url) as conn:
        for product in products:
            summary = ingest_product(
                conn,
                client,
                product=product,
                area=area,
                day_range=day_range,
                start_date=args.date,
            )
            summaries.append(summary.as_dict())

    if args.json:
        print(json.dumps(summaries, indent=2, default=str))

    failed = [s for s in summaries if s["status"] == "failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
