"""Application configuration.

Settings are read lazily via get_settings() — nothing connects to a database or
requires credentials at import time, so the modules stay importable in tests and CI.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = Field(alias="DATABASE_URL")
    nasa_firms_map_key: str = Field(alias="NASA_FIRMS_MAP_KEY")

    # No default area is supplied on purpose: the ingest scope is a deliberate
    # choice (a world VIIRS query returns 30k-100k+ rows/day), so it must be set
    # explicitly in .env or passed with --area.
    firms_area: str | None = Field(default=None, alias="FIRMS_AREA")
    firms_products: str = Field(
        default="VIIRS_NOAA20_NRT,VIIRS_SNPP_NRT,VIIRS_NOAA21_NRT", alias="FIRMS_PRODUCTS"
    )
    firms_day_range: int = Field(default=1, alias="FIRMS_DAY_RANGE")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def product_list(self) -> list[str]:
        return [p.strip() for p in self.firms_products.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
