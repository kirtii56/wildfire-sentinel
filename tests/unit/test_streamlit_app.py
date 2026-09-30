"""Smoke test for the Streamlit dashboard, using the synthetic fixture instead of NASA."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

import app.ingestion.firms_client as firms_client  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "synthetic_viirs_valid.csv"


class _FakeResult:
    # Moved to today's UTC date so the rolling time window keeps the rows.
    csv_text = FIXTURE.read_text().replace("2026-06-12", datetime.now(UTC).strftime("%Y-%m-%d"))


class _FakeClient:
    def __init__(self, map_key: str) -> None:
        pass

    def fetch(self, product, area, day_range):
        return _FakeResult()


def test_dashboard_renders_with_synthetic_data(monkeypatch):
    monkeypatch.setattr(firms_client, "FirmsClient", _FakeClient)
    monkeypatch.setenv("NASA_FIRMS_MAP_KEY", "test-key")
    import streamlit as st

    st.cache_data.clear()
    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=60).run()

    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Fire events"] == "4"
    # 4 fixture rows x 3 satellites
    assert any("from 12 satellite detections" in c.value for c in at.caption)


def test_dashboard_explains_missing_key(monkeypatch):
    monkeypatch.setenv("NASA_FIRMS_MAP_KEY", "your_map_key_here")
    import streamlit as st

    st.cache_data.clear()
    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=60).run()

    assert not at.exception
    assert any("NASA_FIRMS_MAP_KEY is missing" in e.value for e in at.error)
