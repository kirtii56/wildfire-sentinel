"""Writes to the fire_detections, ingest_runs and ingest_rejects tables.

Inserts are batched: one executemany per batch, not one round-trip per record.
Deduplication is delegated to the uq_observation constraint via
ON CONFLICT DO NOTHING, so re-ingesting an overlapping day range is idempotent.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg

from app.core.logging import get_logger

_logger = get_logger("loader")

DETECTION_COLUMNS: tuple[str, ...] = (
    "ingest_run_id",
    "product",
    "instrument",
    "satellite",
    "version",
    "latitude",
    "longitude",
    "acq_date",
    "acq_time_utc",
    "brightness_k",
    "brightness_channel",
    "brightness_secondary_k",
    "brightness_secondary_channel",
    "frp_mw",
    "scan_km",
    "track_km",
    "confidence_text",
    "confidence_pct",
    "daynight",
)

_PLACEHOLDERS = ", ".join(["%s"] * len(DETECTION_COLUMNS))
INSERT_DETECTION_SQL = (
    f"INSERT INTO fire_detections ({', '.join(DETECTION_COLUMNS)}) "
    f"VALUES ({_PLACEHOLDERS}) "
    f"ON CONFLICT ON CONSTRAINT uq_observation DO NOTHING"
)


@dataclass(frozen=True)
class InsertResult:
    attempted: int
    inserted: int

    @property
    def skipped_duplicate(self) -> int:
        """Rows the unique constraint absorbed.

        Includes duplicates already in the table and duplicates repeated within
        the same batch.
        """
        return self.attempted - self.inserted


def start_ingest_run(
    conn: psycopg.Connection,
    *,
    product: str,
    area: str,
    day_range: int,
    request_url: str,
    request_date: str | None = None,
) -> int:
    """Create the run row. request_url must already have the MAP_KEY redacted."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingest_runs (product, area, day_range, request_date, request_url, status)
            VALUES (%s, %s, %s, %s, %s, 'running')
            RETURNING id
            """,
            (product, area, day_range, request_date, request_url),
        )
        row = cur.fetchone()
    conn.commit()
    return int(row["id"])


def finish_ingest_run(
    conn: psycopg.Connection,
    run_id: int,
    *,
    status: str,
    rows_fetched: int = 0,
    rows_accepted: int = 0,
    rows_rejected: int = 0,
    rows_inserted: int = 0,
    rows_duplicate: int = 0,
    error_detail: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingest_runs
               SET status = %s,
                   finished_at = %s,
                   rows_fetched = %s,
                   rows_accepted = %s,
                   rows_rejected = %s,
                   rows_inserted = %s,
                   rows_duplicate = %s,
                   error_detail = %s
             WHERE id = %s
            """,
            (
                status,
                datetime.now(UTC),
                rows_fetched,
                rows_accepted,
                rows_rejected,
                rows_inserted,
                rows_duplicate,
                error_detail,
                run_id,
            ),
        )
    conn.commit()


def insert_detections(
    conn: psycopg.Connection,
    run_id: int,
    records: Sequence[dict[str, Any]],
    batch_size: int = 1000,
) -> InsertResult:
    if not records:
        return InsertResult(attempted=0, inserted=0)

    inserted = 0
    with conn.cursor() as cur:
        for start in range(0, len(records), batch_size):
            batch = records[start : start + batch_size]
            params = [
                tuple(
                    run_id if column == "ingest_run_id" else record.get(column)
                    for column in DETECTION_COLUMNS
                )
                for record in batch
            ]
            cur.executemany(INSERT_DETECTION_SQL, params)
            inserted += max(cur.rowcount, 0)
            _logger.debug("Batch of %s rows: %s inserted so far", len(batch), inserted)
    conn.commit()
    return InsertResult(attempted=len(records), inserted=inserted)


def insert_rejects(
    conn: psycopg.Connection,
    run_id: int,
    rejects: Sequence[tuple[int, str, dict[str, str]]],
    batch_size: int = 1000,
) -> int:
    if not rejects:
        return 0
    with conn.cursor() as cur:
        for start in range(0, len(rejects), batch_size):
            batch = rejects[start : start + batch_size]
            cur.executemany(
                """
                INSERT INTO ingest_rejects (ingest_run_id, row_number, reason, raw_row)
                VALUES (%s, %s, %s, %s)
                """,
                [
                    (run_id, row_number, reason, json.dumps(raw))
                    for row_number, reason, raw in batch
                ],
            )
    conn.commit()
    return len(rejects)
