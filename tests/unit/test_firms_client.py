from __future__ import annotations

import httpx
import pytest
import respx

from app.ingestion.firms_client import (
    FirmsClient,
    FirmsError,
    FirmsResponseError,
    build_url,
    redact,
)

MAP_KEY = "test_map_key_do_not_use_0123456789"
PRODUCT = "VIIRS_NOAA20_NRT"
AREA = "68,6,98,38"


def _client(**kwargs) -> FirmsClient:
    kwargs.setdefault("sleep", lambda _seconds: None)
    return FirmsClient(map_key=MAP_KEY, **kwargs)


def test_build_url_follows_the_firms_area_csv_shape():
    url = build_url(MAP_KEY, PRODUCT, AREA, 1)
    assert url.endswith(f"/{MAP_KEY}/{PRODUCT}/{AREA}/1")
    assert build_url(MAP_KEY, PRODUCT, AREA, 2, "2026-06-12").endswith("/2/2026-06-12")


@pytest.mark.parametrize("day_range", [0, 11, -1])
def test_day_range_outside_the_firms_limit_is_rejected(day_range):
    with pytest.raises(ValueError):
        build_url(MAP_KEY, PRODUCT, AREA, day_range)


def test_redact_removes_the_map_key():
    assert MAP_KEY not in redact(build_url(MAP_KEY, PRODUCT, AREA, 1), MAP_KEY)
    assert "REDACTED" in _client().redacted_url(PRODUCT, AREA, 1)


@respx.mock
def test_fetch_returns_the_csv_body_and_a_redacted_url(fixtures_dir):
    csv_text = (fixtures_dir / "synthetic_viirs_valid.csv").read_text()
    respx.get(build_url(MAP_KEY, PRODUCT, AREA, 1)).mock(
        return_value=httpx.Response(200, text=csv_text)
    )

    result = _client().fetch(PRODUCT, AREA, 1)

    assert result.csv_text == csv_text
    assert MAP_KEY not in result.redacted_url


@respx.mock
def test_non_csv_body_with_http_200_is_an_error_not_data(fixtures_dir):
    body = (fixtures_dir / "synthetic_firms_error_body.txt").read_text()
    respx.get(build_url(MAP_KEY, PRODUCT, AREA, 1)).mock(
        return_value=httpx.Response(200, text=body)
    )

    with pytest.raises(FirmsResponseError) as excinfo:
        _client().fetch(PRODUCT, AREA, 1)

    assert MAP_KEY not in str(excinfo.value)


@respx.mock
def test_client_errors_are_not_retried():
    route = respx.get(build_url(MAP_KEY, PRODUCT, AREA, 1)).mock(return_value=httpx.Response(404))

    with pytest.raises(FirmsError):
        _client().fetch(PRODUCT, AREA, 1)

    assert route.call_count == 1


@respx.mock
def test_server_errors_are_retried_then_give_up():
    route = respx.get(build_url(MAP_KEY, PRODUCT, AREA, 1)).mock(return_value=httpx.Response(503))

    with pytest.raises(FirmsError):
        _client(max_retries=3).fetch(PRODUCT, AREA, 1)

    assert route.call_count == 3


@respx.mock
def test_timeouts_are_retried_and_a_later_attempt_can_succeed(fixtures_dir):
    csv_text = (fixtures_dir / "synthetic_viirs_valid.csv").read_text()
    route = respx.get(build_url(MAP_KEY, PRODUCT, AREA, 1)).mock(
        side_effect=[
            httpx.TimeoutException("timed out"),
            httpx.Response(200, text=csv_text),
        ]
    )

    result = _client(max_retries=3).fetch(PRODUCT, AREA, 1)

    assert route.call_count == 2
    assert result.status_code == 200
