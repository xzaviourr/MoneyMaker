"""
Simple Yahoo Finance cache.
Stores downloaded data in memory with TTL so we never hammer the API.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import structlog

log = structlog.get_logger(__name__)

_cache: dict[str, tuple[float, Any]] = {}  # key -> (timestamp, data)

# How long to keep cached data (30 minutes)
_TTL_SECONDS = 1800

# Block ALL yfinance calls for this many seconds after a 401 error
_BACKOFF_SECONDS = 3600  # 1 hour
_blocked_until: float = 0.0


def is_blocked() -> bool:
    return time.time() < _blocked_until


def trigger_backoff() -> None:
    global _blocked_until
    _blocked_until = time.time() + _BACKOFF_SECONDS
    log.warning(
        "yf_cache.backoff_triggered",
        resume_in_minutes=_BACKOFF_SECONDS // 60,
    )


def get(key: str) -> Optional[Any]:
    if key not in _cache:
        return None
    ts, data = _cache[key]
    if time.time() - ts > _TTL_SECONDS:
        del _cache[key]
        return None
    return data


def set(key: str, data: Any) -> None:
    _cache[key] = (time.time(), data)


def download(ticker: str, **kwargs) -> Any:
    """Wrapper around yf.download with caching and backoff."""
    import yfinance as yf

    if is_blocked():
        log.debug("yf_cache.blocked", ticker=ticker)
        return None

    key = f"download:{ticker}:{kwargs.get('period','60d')}:{kwargs.get('interval','1d')}"
    cached = get(key)
    if cached is not None:
        return cached

    try:
        data = yf.download(ticker, progress=False, **kwargs)
        if data is not None and not data.empty:
            set(key, data)
        return data
    except Exception as exc:
        err = str(exc).lower()
        if "401" in err or "unauthorized" in err or "crumb" in err:
            trigger_backoff()
        return None


def ticker_info(symbol: str) -> Any:
    """Wrapper around yf.Ticker with caching."""
    import yfinance as yf

    if is_blocked():
        return None

    key = f"info:{symbol}"
    cached = get(key)
    if cached is not None:
        return cached

    try:
        t = yf.Ticker(symbol)
        info = t.fast_info
        set(key, info)
        return info
    except Exception as exc:
        err = str(exc).lower()
        if "401" in err or "unauthorized" in err:
            trigger_backoff()
        return None
