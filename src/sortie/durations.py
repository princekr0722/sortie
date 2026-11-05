"""Turn start dates into experience durations, computed as of the run date."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Duration:
    months: int

    @classmethod
    def since(cls, start: date, today: date | None = None) -> Duration:
        today = today or date.today()
        months = (today.year - start.year) * 12 + (today.month - start.month)
        if today.day < start.day:
            months -= 1
        return cls(max(months, 0))

    @property
    def years(self) -> int:
        return self.months // 12

    @property
    def remaining_months(self) -> int:
        return self.months % 12

    @property
    def decimal_years(self) -> float:
        """One decimal place, rounded down (38 months -> 3.1)."""
        return (self.months * 10 // 12) / 10

    def as_text(self) -> str:
        parts = []
        if self.years:
            parts.append(f"{self.years} year{'s' if self.years != 1 else ''}")
        if self.remaining_months or not parts:
            m = self.remaining_months
            parts.append(f"{m} month{'s' if m != 1 else ''}")
        return " ".join(parts)


ZERO = Duration(0)
