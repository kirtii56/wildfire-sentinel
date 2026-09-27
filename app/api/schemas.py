"""Response models. FastAPI uses these to validate output and document the API."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class Health(BaseModel):
    status: str
    database: str


class Detection(BaseModel):
    id: int
    latitude: float
    longitude: float
    acquired_at: datetime
    satellite: str
    product: str
    frp_mw: float | None = Field(None, description="Fire radiative power, megawatts")
    confidence_text: str | None = None
    daynight: str | None = None


class FireEvent(BaseModel):
    event_id: int
    n_detections: int = Field(description="Satellite pixels grouped into this event")
    centroid_lat: float
    centroid_lon: float
    first_seen: datetime
    last_seen: datetime
    duration_hours: float
    total_frp_mw: float | None = None
    max_frp_mw: float | None = None
    pixel_footprint_km2: float | None = Field(
        None, description="Summed pixel area. Not burned area."
    )


class FireEventList(BaseModel):
    window_start: datetime
    window_end: datetime
    total_detections: int
    total_events: int
    events: list[FireEvent]


class DailyStat(BaseModel):
    day: date
    product: str
    detections: int
    total_frp_mw: float | None = None


class IngestRun(BaseModel):
    id: int
    product: str
    area: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    rows_fetched: int
    rows_inserted: int
    rows_duplicate: int
    rows_rejected: int
