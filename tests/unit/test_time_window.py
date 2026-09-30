"""Rolling-window tests."""

from __future__ import annotations

import pandas as pd
import pytest

from app.analysis.time_window import firms_day_range, keep_last_days

NOW = pd.Timestamp("2026-09-30T02:30:00Z")


def _df(*hours_ago):
    return pd.DataFrame({"acquired_at": [NOW - pd.Timedelta(hours=h) for h in hours_ago]})


def test_requests_one_extra_calendar_day():
    assert firms_day_range(1) == 2
    assert firms_day_range(3) == 4


@pytest.mark.parametrize("days", [0, 10])
def test_rejects_out_of_range(days):
    with pytest.raises(ValueError):
        firms_day_range(days)


def test_keeps_exactly_last_24_hours_across_midnight():
    # 02:30 UTC: 1 h ago is today, 20 h ago is yesterday, 30 h ago is too old.
    kept = keep_last_days(_df(1, 20, 30), days=1, now=NOW)
    assert len(kept) == 2


def test_longer_window():
    assert len(keep_last_days(_df(1, 30, 60, 80), days=3, now=NOW)) == 3


def test_empty_input():
    assert keep_last_days(pd.DataFrame(columns=["acquired_at"]), days=1, now=NOW).empty
