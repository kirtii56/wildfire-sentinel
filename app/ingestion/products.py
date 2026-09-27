"""Registry of supported NASA FIRMS products.

Each FIRMS product returns the same CSV shape but names its thermal channels
differently and expresses confidence differently. Those differences are declared
here rather than branched through the parser, so adding a product is one entry.

Column names verified against the FIRMS VIIRS sample CSV documented at
https://firms.modaps.eosdis.nasa.gov/content/academy/data_ingest/firms_data_ingest.html
(latitude, longitude, bright_ti4, scan, track, acq_date, acq_time, satellite,
instrument, confidence, version, bright_ti5, frp, daynight).

The channel stored in the database is the FIRMS column name itself, so a
brightness value is never separated from the channel that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ConfidenceKind = Literal["categorical", "percent"]


class UnsupportedProductError(ValueError):
    """Raised when a FIRMS product key has no registry entry."""


@dataclass(frozen=True)
class ProductSpec:
    key: str
    brightness_column: str
    secondary_brightness_column: str
    confidence_kind: ConfidenceKind


_VIIRS = {
    "brightness_column": "bright_ti4",
    "secondary_brightness_column": "bright_ti5",
    "confidence_kind": "categorical",
}

# Scope: VIIRS NRT only (S-NPP, NOAA-20, NOAA-21). MODIS uses brightness/bright_t31
# and a 0-100 percentage confidence; it gets an entry here when it is approved,
# not before.
SUPPORTED_PRODUCTS: dict[str, ProductSpec] = {
    "VIIRS_NOAA20_NRT": ProductSpec(key="VIIRS_NOAA20_NRT", **_VIIRS),  # type: ignore[arg-type]
    "VIIRS_SNPP_NRT": ProductSpec(key="VIIRS_SNPP_NRT", **_VIIRS),  # type: ignore[arg-type]
    # NOAA-21 carries the same VIIRS instrument and FIRMS returns the same CSV
    # columns for it, so it reuses the VIIRS spec unchanged.
    "VIIRS_NOAA21_NRT": ProductSpec(key="VIIRS_NOAA21_NRT", **_VIIRS),  # type: ignore[arg-type]
}


def get_product(key: str) -> ProductSpec:
    try:
        return SUPPORTED_PRODUCTS[key]
    except KeyError:
        supported = ", ".join(sorted(SUPPORTED_PRODUCTS))
        raise UnsupportedProductError(
            f"Unsupported FIRMS product {key!r}. Supported: {supported}"
        ) from None
