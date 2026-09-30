"""Compare the chosen time window with the window just before it."""

from __future__ import annotations

import math

import pandas as pd


def split_periods(
    detections: pd.DataFrame, days: int, now: pd.Timestamp
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(current, previous): the last ``days`` x 24 h, and the ``days`` x 24 h before that."""
    cutoff = now - pd.Timedelta(days=days)
    earliest = cutoff - pd.Timedelta(days=days)
    at = detections["acquired_at"]
    current = detections[at >= cutoff]
    previous = detections[(at >= earliest) & (at < cutoff)]
    return current, previous


def pct_change(current: float, previous: float) -> float | None:
    """Percentage change, or None when there is nothing to compare against."""
    if previous is None or previous == 0 or math.isnan(previous):
        return None
    return (current - previous) / previous * 100.0


def format_delta(change: float | None, period: str) -> str | None:
    """'+18% vs previous 24 h' (None hides the delta)."""
    if change is None:
        return None
    return f"{change:+.0f}% vs previous {period}"
