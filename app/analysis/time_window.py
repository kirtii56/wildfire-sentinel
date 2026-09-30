"""Turn NASA FIRMS calendar-day windows into true rolling windows.

FIRMS counts ``day_range`` in UTC calendar days, where day 1 is "today so far".
Early in the UTC day that is only a few hours of data, so a "last 24 hours" view
built from ``day_range=1`` looks almost empty. The fix: ask FIRMS for one extra
day, then keep exactly the last ``days`` x 24 hours.
"""

from __future__ import annotations

import pandas as pd

FIRMS_MAX_DAY_RANGE = 10


def firms_day_range(days: int) -> int:
    """The FIRMS day_range to request so that a rolling ``days`` window is fully covered."""
    if not 1 <= days < FIRMS_MAX_DAY_RANGE:
        raise ValueError(f"days must be between 1 and {FIRMS_MAX_DAY_RANGE - 1}")
    return days + 1


def keep_last_days(
    detections: pd.DataFrame, days: int, now: pd.Timestamp | None = None
) -> pd.DataFrame:
    """Rows whose ``acquired_at`` falls within the last ``days`` x 24 hours."""
    if detections.empty:
        return detections
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    cutoff = now - pd.Timedelta(days=days)
    return detections[detections["acquired_at"] >= cutoff].reset_index(drop=True)
