"""NSE market hours — used to gate intraday pod trading to real trading hours.

Uses a fixed UTC+5:30 offset rather than zoneinfo("Asia/Kolkata") — India has no
DST so the offset never changes, and this Windows Python install has no tzdata
package, which would otherwise raise ZoneInfoNotFoundError on every call.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

_IST = timezone(timedelta(hours=5, minutes=30))

from .config import toml_cfg


def is_market_open(now: datetime | None = None) -> bool:
    cfg = toml_cfg.get("market_hours", {})
    if not cfg.get("enabled", True):
        return True

    now = (now or datetime.now(_IST)).astimezone(_IST)
    if now.weekday() >= 5:  # Saturday/Sunday
        return False

    open_t  = time.fromisoformat(cfg.get("open", "09:15"))
    close_t = time.fromisoformat(cfg.get("close", "15:30"))
    return open_t <= now.time() <= close_t
