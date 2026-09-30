"""quick_map.py end-to-end with NASA mocked (synthetic fixture data only)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import respx

from scripts import quick_map


def test_quick_map_end_to_end(fixtures_dir, monkeypatch, tmp_path):
    # Move the fixture to today's UTC date so the rolling 24-hour window keeps it.
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    csv_text = (fixtures_dir / "synthetic_viirs_valid.csv").read_text().replace("2026-06-12", today)
    monkeypatch.setenv("NASA_FIRMS_MAP_KEY", "TESTKEY")
    monkeypatch.setattr(quick_map, "ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["quick_map.py"])

    with respx.mock:
        route = respx.get(url__startswith="https://firms.modaps.eosdis.nasa.gov/").mock(
            return_value=httpx.Response(200, text=csv_text)
        )
        assert quick_map.main() == 0
        assert route.call_count == 3  # one request per VIIRS satellite

    assert (tmp_path / "output" / "fire_map.html").stat().st_size > 1000
    events = (tmp_path / "output" / "fire_events.csv").read_text().splitlines()
    assert events[0].startswith("event_id,")
    assert len(events) > 1
