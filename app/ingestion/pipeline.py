"""Ingestion orchestration: fetch -> parse -> validate -> load, per product.

Every stage records what actually happened in ingest_runs. A run that fetched
zero rows without error is a success, not a failure: v1 reported 'failed'
whenever nothing was stored, which made an empty-but-correct run look broken.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import psycopg

from app.core.logging import get_logger
from app.database import loader
from app.ingestion.firms_client import FirmsClient
from app.ingestion.parser import parse_csv
from app.ingestion.products import get_product
from app.ingestion.validation import validate_records

_logger = get_logger("pipeline")


@dataclass
class IngestSummary:
    run_id: int
    product: str
    area: str
    day_range: int
    status: str
    rows_fetched: int = 0
    rows_accepted: int = 0
    rows_rejected: int = 0
    rows_inserted: int = 0
    rows_duplicate: int = 0
    error_detail: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def ingest_product(
    conn: psycopg.Connection,
    client: FirmsClient,
    *,
    product: str,
    area: str,
    day_range: int,
    start_date: str | None = None,
) -> IngestSummary:
    spec = get_product(product)

    # Build the redacted URL before any request so the run row can be created
    # even if the request itself fails.
    redacted_url = client.redacted_url(product, area, day_range, start_date)
    run_id = loader.start_ingest_run(
        conn,
        product=product,
        area=area,
        day_range=day_range,
        request_url=redacted_url,
        request_date=start_date,
    )
    summary = IngestSummary(
        run_id=run_id, product=product, area=area, day_range=day_range, status="running"
    )

    try:
        fetched = client.fetch(product, area, day_range, start_date)
        raw_records = parse_csv(fetched.csv_text, spec)
        summary.rows_fetched = len(raw_records)

        outcome = validate_records(raw_records, spec)
        summary.rows_accepted, summary.rows_rejected = outcome.counts

        result = loader.insert_detections(conn, run_id, outcome.accepted)
        summary.rows_inserted = result.inserted
        summary.rows_duplicate = result.skipped_duplicate

        loader.insert_rejects(conn, run_id, outcome.rejected)

        summary.status = "partial" if summary.rows_rejected else "success"

    except Exception as error:  # noqa: BLE001 - recorded, re-raised by the caller's choice
        conn.rollback()
        summary.status = "failed"
        summary.error_detail = f"{type(error).__name__}: {error}"
        _logger.error("Ingest run %s failed: %s", run_id, summary.error_detail)

    loader.finish_ingest_run(
        conn,
        run_id,
        status=summary.status,
        rows_fetched=summary.rows_fetched,
        rows_accepted=summary.rows_accepted,
        rows_rejected=summary.rows_rejected,
        rows_inserted=summary.rows_inserted,
        rows_duplicate=summary.rows_duplicate,
        error_detail=summary.error_detail,
    )
    _logger.info(
        "Run %s (%s): fetched=%s accepted=%s rejected=%s inserted=%s duplicate=%s status=%s",
        run_id,
        product,
        summary.rows_fetched,
        summary.rows_accepted,
        summary.rows_rejected,
        summary.rows_inserted,
        summary.rows_duplicate,
        summary.status,
    )
    return summary
