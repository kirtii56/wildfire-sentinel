from __future__ import annotations

from datetime import date, time

import pytest

from app.ingestion.parser import parse_csv
from app.ingestion.products import ProductSpec, get_product
from app.ingestion.validation import RowRejected, validate_records, validate_row

SPEC = get_product("VIIRS_NOAA20_NRT")

MODIS_SHAPED_SPEC = ProductSpec(
    key="MODIS_SHAPED_TEST_ONLY",
    brightness_column="brightness",
    secondary_brightness_column="bright_t31",
    confidence_kind="percent",
)


def _records(fixtures_dir, name, spec=SPEC):
    return parse_csv((fixtures_dir / name).read_text(), spec)


def test_valid_rows_convert_with_nasa_semantics_preserved(fixtures_dir):
    outcome = validate_records(_records(fixtures_dir, "synthetic_viirs_valid.csv"), SPEC)
    assert outcome.counts == (4, 0)

    first = outcome.accepted[0]
    assert first["latitude"] == -12.345
    assert first["acq_date"] == date(2026, 6, 12)
    assert first["acq_time_utc"] == time(13, 45)
    assert first["confidence_text"] == "nominal"
    assert first["confidence_pct"] is None
    assert first["brightness_channel"] == "bright_ti4"
    assert first["brightness_secondary_channel"] == "bright_ti5"


def test_three_digit_acq_time_is_padded_to_hhmm(fixtures_dir):
    outcome = validate_records(_records(fixtures_dir, "synthetic_viirs_valid.csv"), SPEC)
    assert outcome.accepted[2]["acq_time_utc"] == time(3, 45)


def test_absent_measurements_become_null_never_defaulted(fixtures_dir):
    outcome = validate_records(_records(fixtures_dir, "synthetic_viirs_valid.csv"), SPEC)
    sparse = outcome.accepted[3]
    assert sparse["scan_km"] is None
    assert sparse["track_km"] is None
    assert sparse["frp_mw"] is None
    assert sparse["brightness_secondary_k"] is None
    assert sparse["brightness_secondary_channel"] is None


def test_abbreviated_viirs_confidence_maps_to_nasa_category(fixtures_dir):
    outcome = validate_records(_records(fixtures_dir, "synthetic_viirs_valid.csv"), SPEC)
    assert outcome.accepted[3]["confidence_text"] == "high"


def test_every_invalid_row_is_rejected_with_a_reason(fixtures_dir):
    outcome = validate_records(_records(fixtures_dir, "synthetic_viirs_invalid.csv"), SPEC)
    assert outcome.counts == (0, 9)
    reasons = [reason for _, reason, _ in outcome.rejected]
    assert "missing required field 'latitude'" in reasons[0]
    assert "latitude out of range" in reasons[1]
    assert "longitude out of range" in reasons[2]
    assert "acq_time is out of range" in reasons[3]
    assert "unrecognised VIIRS confidence" in reasons[4]
    assert "frp is negative" in reasons[5]
    assert "not numeric" in reasons[6]
    assert "acq_date is not an ISO date" in reasons[7]
    assert "missing required field 'satellite'" in reasons[8]


def test_missing_coordinate_is_never_replaced_with_zero():
    raw = {
        "latitude": "",
        "longitude": "-67.89",
        "acq_date": "2026-06-12",
        "acq_time": "1345",
        "satellite": "N20",
        "instrument": "VIIRS",
        "confidence": "nominal",
    }
    with pytest.raises(RowRejected):
        validate_row(raw, SPEC)


def test_percent_confidence_products_keep_the_numeric_value(fixtures_dir):
    records = _records(fixtures_dir, "synthetic_modis_shaped.csv", MODIS_SHAPED_SPEC)
    outcome = validate_records(records, MODIS_SHAPED_SPEC)
    assert outcome.counts == (1, 1)
    assert outcome.accepted[0]["confidence_pct"] == 74
    assert outcome.accepted[0]["confidence_text"] is None
    assert outcome.accepted[0]["brightness_channel"] == "brightness"
    assert "out of range" in outcome.rejected[0][1]
