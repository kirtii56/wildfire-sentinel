"""Product registry tests."""

from __future__ import annotations

import pytest

from app.ingestion.parser import parse_csv
from app.ingestion.products import SUPPORTED_PRODUCTS, UnsupportedProductError, get_product

VIIRS_KEYS = ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT")


@pytest.mark.parametrize("key", VIIRS_KEYS)
def test_all_viirs_products_supported_with_same_columns(key):
    spec = get_product(key)
    assert spec.brightness_column == "bright_ti4"
    assert spec.secondary_brightness_column == "bright_ti5"
    assert spec.confidence_kind == "categorical"


def test_noaa21_csv_parses(fixtures_dir):
    csv_text = (fixtures_dir / "synthetic_viirs_valid.csv").read_text().replace(",N20,", ",N21,")
    rows = parse_csv(csv_text, get_product("VIIRS_NOAA21_NRT"))
    assert rows and all(r["satellite"] == "N21" for r in rows)


def test_modis_not_supported():
    assert "MODIS_NRT" not in SUPPORTED_PRODUCTS
    with pytest.raises(UnsupportedProductError):
        get_product("MODIS_NRT")
