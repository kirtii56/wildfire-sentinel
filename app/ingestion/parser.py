"""Parse a FIRMS CSV response into raw string records.

Everything is read as text here. Type conversion happens in validation.py, where
a failure produces an explicit rejection instead of a silent default.
"""

from __future__ import annotations

from io import StringIO

import pandas as pd

from app.ingestion.products import ProductSpec

BASE_REQUIRED_COLUMNS = (
    "latitude",
    "longitude",
    "acq_date",
    "acq_time",
    "satellite",
    "instrument",
    "confidence",
)


class FirmsSchemaError(ValueError):
    """The CSV did not contain the columns this product is supposed to return."""


def parse_csv(csv_text: str, spec: ProductSpec) -> list[dict[str, str]]:
    frame = pd.read_csv(
        StringIO(csv_text),
        dtype=str,
        keep_default_na=False,
        na_values=[],
    )

    expected = (*BASE_REQUIRED_COLUMNS, spec.brightness_column)
    missing = [column for column in expected if column not in frame.columns]
    if missing:
        raise FirmsSchemaError(
            f"FIRMS response for {spec.key} is missing expected column(s): "
            f"{', '.join(missing)}. Columns present: {', '.join(frame.columns)}"
        )

    return frame.to_dict("records")
