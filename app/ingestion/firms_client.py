"""HTTP client for the NASA FIRMS area/csv API.

The MAP_KEY appears in the request path, so every URL that leaves this module
(for logging or for storage in ingest_runs.request_url) is redacted first.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from app.core.logging import get_logger

FIRMS_BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
REDACTION = "REDACTED"

_logger = get_logger("firms_client")


class FirmsError(RuntimeError):
    """Base class for FIRMS request failures."""


class FirmsResponseError(FirmsError):
    """The endpoint returned 200 but the body was not a FIRMS CSV.

    FIRMS answers some failures (invalid key, exhausted transaction quota) with a
    plain-text message and a 200 status, so a successful HTTP status is not on its
    own evidence that we received data.
    """


@dataclass(frozen=True)
class FetchResult:
    csv_text: str
    redacted_url: str
    status_code: int


def build_url(
    map_key: str,
    product: str,
    area: str,
    day_range: int,
    start_date: str | None = None,
) -> str:
    if not 1 <= day_range <= 10:
        raise ValueError("day_range must be between 1 and 10 (FIRMS limit)")
    url = f"{FIRMS_BASE_URL}/{map_key}/{product}/{area}/{day_range}"
    if start_date:
        url = f"{url}/{start_date}"
    return url


def redact(text: str, map_key: str) -> str:
    """Remove the MAP_KEY from a URL or message before logging or storing it."""
    if not map_key:
        return text
    return text.replace(map_key, REDACTION)


class FirmsClient:
    def __init__(
        self,
        map_key: str,
        timeout: float = 60.0,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._map_key = map_key
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep

    def fetch(
        self,
        product: str,
        area: str,
        day_range: int,
        start_date: str | None = None,
    ) -> FetchResult:
        url = build_url(self._map_key, product, area, day_range, start_date)
        safe_url = redact(url, self._map_key)

        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = httpx.get(url, timeout=self._timeout)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_error = exc
                _logger.warning(
                    "Attempt %s/%s failed for %s: %s",
                    attempt,
                    self._max_retries,
                    safe_url,
                    type(exc).__name__,
                )
            else:
                if response.status_code >= 500:
                    last_error = httpx.HTTPStatusError(
                        f"server error {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                    _logger.warning(
                        "Attempt %s/%s got HTTP %s for %s",
                        attempt,
                        self._max_retries,
                        response.status_code,
                        safe_url,
                    )
                elif response.status_code >= 400:
                    # Client errors are not retried: a bad key or bad area will
                    # fail identically on every attempt.
                    raise FirmsError(f"FIRMS returned HTTP {response.status_code} for {safe_url}")
                else:
                    self._assert_csv_body(response.text, safe_url)
                    return FetchResult(
                        csv_text=response.text,
                        redacted_url=safe_url,
                        status_code=response.status_code,
                    )

            if attempt < self._max_retries:
                self._sleep(self._backoff_base**attempt)

        raise FirmsError(
            f"FIRMS request failed after {self._max_retries} attempts for {safe_url}"
        ) from last_error

    def redacted_url(
        self,
        product: str,
        area: str,
        day_range: int,
        start_date: str | None = None,
    ) -> str:
        """The URL this client would request, safe to log or store."""
        return redact(build_url(self._map_key, product, area, day_range, start_date), self._map_key)

    def _assert_csv_body(self, body: str, safe_url: str) -> None:
        first_line = body.lstrip().split("\n", 1)[0]
        if "latitude" not in first_line.lower():
            snippet = redact(body.strip()[:200], self._map_key)
            raise FirmsResponseError(f"FIRMS returned a non-CSV body for {safe_url}: {snippet!r}")
