from __future__ import annotations

from datetime import datetime
from typing import Any


def float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_datetime(value: str | None) -> datetime:
    if not value:
        raise ValueError("Replay fill rows require timestamp, filled_at, or fill_time")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


__all__ = ["float_or_none", "parse_datetime"]
