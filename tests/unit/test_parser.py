from __future__ import annotations

import pytest

from app.ingestion.parser import FirmsSchemaError, parse_csv
from app.ingestion.products import get_product

SPEC = get_product("VIIRS_NOAA20_NRT")


def test_parses_every_row_as_strings(fixtures_dir):
    records = parse_csv((fixtures_dir / "synthetic_viirs_valid.csv").read_text(), SPEC)
    assert len(records) == 4
    assert all(isinstance(value, str) for value in records[0].values())


def test_absent_optional_values_stay_empty_not_defaulted(fixtures_dir):
    records = parse_csv((fixtures_dir / "synthetic_viirs_valid.csv").read_text(), SPEC)
    sparse = records[3]
    assert sparse["scan"] == ""
    assert sparse["track"] == ""
    assert sparse["frp"] == ""


def test_missing_expected_column_raises_rather_than_silently_continuing(fixtures_dir):
    text = (fixtures_dir / "synthetic_viirs_missing_column.csv").read_text()
    with pytest.raises(FirmsSchemaError, match="bright_ti4"):
        parse_csv(text, SPEC)
