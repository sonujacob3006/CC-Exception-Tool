"""
Cycle Count Scheduler — Al Meera Consumer Goods
Calendar logic: excludes Fri, Sat, 2nd & 4th Thursday, national holidays.
Rotation memory: tracks which SKUs were last flagged and on which date.
"""

import json
import os
from datetime import date, timedelta
import pandas as pd


# Qatar National Day is 18 Dec — if it falls Fri/Sat it's already excluded.
QATAR_HOLIDAYS_2026 = [
    date(2026, 12, 18),  # Qatar National Day
]


def _nth_thursday(year: int, month: int, n: int) -> date:
    """Return the n-th Thursday (1-based) in the given month/year."""
    d = date(year, month, 1)
    # find first Thursday
    days_ahead = 3 - d.weekday()  # Thursday = 3
    if days_ahead < 0:
        days_ahead += 7
    first_thu = d + timedelta(days=days_ahead)
    return first_thu + timedelta(weeks=n - 1)


def is_valid_count_day(d: date, extra_holidays: list[date] | None = None) -> bool:
    """Return True if d is a valid cycle count day."""
    holidays = QATAR_HOLIDAYS_2026 + (extra_holidays or [])
    if d.weekday() in (4, 5):          # Friday=4, Saturday=5
        return False
    if d in holidays:
        return False
    # 2nd and 4th Thursday of the month
    y, m = d.year, d.month
    if d.weekday() == 3:               # Thursday
        if d in (_nth_thursday(y, m, 2), _nth_thursday(y, m, 4)):
            return False
    return True


def build_calendar(start: date, end: date, extra_holidays: list[date] | None = None) -> list[date]:
    """Return list of valid count days between start and end (inclusive)."""
    valid = []
    current = start
    while current <= end:
        if is_valid_count_day(current, extra_holidays):
            valid.append(current)
        current += timedelta(days=1)
    return valid


# ─── ROTATION HISTORY ─────────────────────────────────────────────────────────
class RotationHistory:
    """
    Tracks the last date each Material was flagged.
    Persisted as a JSON file so it survives app restarts.
    """

    def __init__(self, path: str = "rotation_history.json"):
        self.path = path
        self._data: dict[str, str] = {}   # material_str → ISO date str
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}

    def save(self):
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2)

    def days_since(self, material: str, as_of: date) -> int:
        """Days since material was last flagged. Returns 0 if never seen."""
        mat = str(material)
        if mat not in self._data:
            return 0
        last = date.fromisoformat(self._data[mat])
        return max((as_of - last).days, 0)

    def record_flagged(self, materials: list[str], on_date: date):
        """Update last-flagged date for a list of materials."""
        ds = on_date.isoformat()
        for m in materials:
            self._data[str(m)] = ds

    def as_days_dict(self, as_of: date) -> dict[str, int]:
        """Return {material: days_since} for all tracked materials."""
        return {m: self.days_since(m, as_of) for m in self._data}

    def clear(self):
        self._data = {}
        if os.path.exists(self.path):
            os.remove(self.path)

    def to_dataframe(self, as_of: date) -> pd.DataFrame:
        rows = [
            {"Material": m, "Last_Flagged": d, "Days_Since": self.days_since(m, as_of)}
            for m, d in self._data.items()
        ]
        return pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=["Material", "Last_Flagged", "Days_Since"]
        )
