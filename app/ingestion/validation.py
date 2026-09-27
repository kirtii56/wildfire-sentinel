"""Validate and type-convert raw FIRMS records.

Rules, in order of importance:

1. Nothing is ever defaulted, interpolated or invented. A required field that is
   missing or unparseable rejects the row; an optional field that is absent is
   stored as NULL.
2. Confidence keeps NASA's own semantics: VIIRS stays categorical
   (low/nominal/high), MODIS would stay a 0-100 percentage. They are never merged.
3. A brightness value always travels with the name of the channel it came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from typing import Any

from app.ingestion.products import ProductSpec

# FIRMS archive products abbreviate the VIIRS categories; the area CSV spells
# them out. Both encodings mean the same three NASA categories.
_CONFIDENCE_ALIASES = {
    "l": "low",
    "n": "nominal",
    "h": "high",
    "low": "low",
    "nominal": "nominal",
    "high": "high",
}


class RowRejected(ValueError):
    """A single record failed validation and must not be stored."""


@dataclass
class ValidationOutcome:
    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[tuple[int, str, dict[str, str]]] = field(default_factory=list)

    @property
    def counts(self) -> tuple[int, int]:
        return len(self.accepted), len(self.rejected)


def _clean(raw: dict[str, str], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _required_str(raw: dict[str, str], key: str) -> str:
    value = _clean(raw, key)
    if value is None:
        raise RowRejected(f"missing required field '{key}'")
    return value


def _optional_float(raw: dict[str, str], key: str) -> float | None:
    value = _clean(raw, key)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        raise RowRejected(f"field '{key}' is present but not numeric: {value!r}") from None


def _required_float(raw: dict[str, str], key: str) -> float:
    value = _optional_float(raw, key)
    if value is None:
        raise RowRejected(f"missing required field '{key}'")
    return value


def _parse_acq_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise RowRejected(f"acq_date is not an ISO date: {value!r}") from None


def _parse_acq_time(value: str) -> time:
    """FIRMS reports acquisition time as an HHMM integer in UTC (e.g. 1345, 345)."""
    digits = value.strip()
    if not digits.isdigit() or len(digits) > 4:
        raise RowRejected(f"acq_time is not an HHMM value: {value!r}")
    padded = digits.zfill(4)
    hours, minutes = int(padded[:2]), int(padded[2:])
    if hours > 23 or minutes > 59:
        raise RowRejected(f"acq_time is out of range: {value!r}")
    return time(hours, minutes)


def _parse_confidence(raw: dict[str, str], spec: ProductSpec) -> dict[str, Any]:
    value = _required_str(raw, "confidence")
    if spec.confidence_kind == "categorical":
        category = _CONFIDENCE_ALIASES.get(value.lower())
        if category is None:
            raise RowRejected(f"unrecognised VIIRS confidence value: {value!r}")
        return {"confidence_text": category, "confidence_pct": None}

    try:
        percent = int(float(value))
    except ValueError:
        raise RowRejected(f"confidence is not numeric: {value!r}") from None
    if not 0 <= percent <= 100:
        raise RowRejected(f"confidence percentage out of range: {value!r}")
    return {"confidence_text": None, "confidence_pct": percent}


def validate_row(raw: dict[str, str], spec: ProductSpec) -> dict[str, Any]:
    """Convert one raw record, or raise RowRejected with the reason."""
    latitude = _required_float(raw, "latitude")
    longitude = _required_float(raw, "longitude")
    if not -90.0 <= latitude <= 90.0:
        raise RowRejected(f"latitude out of range: {latitude}")
    if not -180.0 <= longitude <= 180.0:
        raise RowRejected(f"longitude out of range: {longitude}")

    frp = _optional_float(raw, "frp")
    if frp is not None and frp < 0:
        raise RowRejected(f"frp is negative: {frp}")

    brightness = _optional_float(raw, spec.brightness_column)
    secondary = _optional_float(raw, spec.secondary_brightness_column)

    daynight = _clean(raw, "daynight")
    if daynight is not None and daynight.upper() not in {"D", "N"}:
        raise RowRejected(f"unrecognised daynight value: {daynight!r}")

    record: dict[str, Any] = {
        "product": spec.key,
        "instrument": _required_str(raw, "instrument"),
        "satellite": _required_str(raw, "satellite"),
        "version": _clean(raw, "version"),
        "latitude": latitude,
        "longitude": longitude,
        "acq_date": _parse_acq_date(_required_str(raw, "acq_date")),
        "acq_time_utc": _parse_acq_time(_required_str(raw, "acq_time")),
        "brightness_k": brightness,
        "brightness_channel": spec.brightness_column if brightness is not None else None,
        "brightness_secondary_k": secondary,
        "brightness_secondary_channel": (
            spec.secondary_brightness_column if secondary is not None else None
        ),
        "frp_mw": frp,
        "scan_km": _optional_float(raw, "scan"),
        "track_km": _optional_float(raw, "track"),
        "daynight": daynight.upper() if daynight else None,
    }
    record.update(_parse_confidence(raw, spec))
    return record


def validate_records(raw_records: list[dict[str, str]], spec: ProductSpec) -> ValidationOutcome:
    outcome = ValidationOutcome()
    for index, raw in enumerate(raw_records, start=1):
        try:
            outcome.accepted.append(validate_row(raw, spec))
        except RowRejected as rejection:
            outcome.rejected.append((index, str(rejection), raw))
    return outcome
