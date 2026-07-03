"""
SQLite-backed market data cache.

Rules:
- Historical daily data (OHLCV) cached for 24 hours
- Intraday data cached for 5 minutes
- Quote (last price) cached for 1 minute
- On 401 error: block all calls for 1 hour

Yahoo Finance free tier limits (approximate):
- ~2000 requests per hour per IP
- No official published limit but rate limiting kicks in above this
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import structlog

from . import feature_toggles
from .service_log import log_event

log = structlog.get_logger(__name__)

_DB_PATH = Path("market_data.db")

# TTL in seconds
_TTL = {
    "daily":    86400,   # 24 hours  — historical OHLCV
    "intraday": 300,     # 5 minutes — intraday bars
    "quote":    60,      # 1 minute  — last price
    "info":     3600,    # 1 hour    — company info
    "calendar": 86400,   # 24 hours  — earnings/dividend calendar, doesn't move intraday
    "holders":  86400,   # 24 hours  — major holders / insider transactions
}

_BACKOFF_SECONDS = 3600   # 1 hour block after 401
_blocked_until:  float = 0.0

# Plain-variable handoff for "a real fetch just happened" — this module is
# sync and called from worker threads (run_in_executor) as well as inline
# from async code, so it can't safely call the async MessageBus directly.
# A small async poller (see main.py) reads this and publishes the event
# instead — no cross-thread asyncio calls needed.
_last_fetch: dict[str, Any] = {"ts": None, "detail": None}


def _note_fetch(detail: str) -> None:
    _last_fetch["ts"] = time.time()
    _last_fetch["detail"] = detail


def get_last_fetch() -> Optional[dict]:
    return dict(_last_fetch) if _last_fetch["ts"] else None


# ── DB setup ───────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_cache (
            key         TEXT PRIMARY KEY,
            data        TEXT NOT NULL,
            fetched_at  REAL NOT NULL,
            ttl         INTEGER NOT NULL
        )
    """)
    conn.commit()
    return conn


def _get(key: str) -> Optional[Any]:
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT data, fetched_at, ttl FROM market_cache WHERE key = ?", (key,)
        ).fetchone()
        conn.close()
        if row is None:
            return None
        data, fetched_at, ttl = row
        if time.time() - fetched_at > ttl:
            return None  # expired
        return json.loads(data)
    except Exception as exc:
        log_event("database", "error", f"Read failed: {key}", {"error": str(exc)})
        return None


def _set(key: str, data: Any, ttl: int) -> None:
    try:
        conn = _get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO market_cache (key, data, fetched_at, ttl)
               VALUES (?, ?, ?, ?)""",
            (key, json.dumps(data, default=str), time.time(), ttl)
        )
        conn.commit()
        conn.close()
        log_event("database", "info", f"Wrote: {key}", {"ttl_seconds": ttl})
    except Exception as e:
        log.warning("market_cache.write_error", error=str(e))
        log_event("database", "error", f"Write failed: {key}", {"error": str(e)})


def _cleanup() -> None:
    """Remove expired entries."""
    try:
        conn = _get_conn()
        conn.execute(
            "DELETE FROM market_cache WHERE (? - fetched_at) > ttl",
            (time.time(),)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── Backoff ────────────────────────────────────────────────────────────────────

def is_blocked() -> bool:
    return time.time() < _blocked_until


def _trigger_backoff() -> None:
    global _blocked_until
    _blocked_until = time.time() + _BACKOFF_SECONDS
    mins = _BACKOFF_SECONDS // 60
    log.warning("market_cache.backoff", resume_in_minutes=mins)
    log_event("yahoo_finance", "warning", f"Rate-limited — blocking calls for {mins} minutes")


# ── Public API ─────────────────────────────────────────────────────────────────

def download(
    ticker: str,
    period: str = "60d",
    interval: str = "1d",
    **kwargs
) -> Optional[Any]:
    """
    Download ticker data with SQLite caching.
    Returns a pandas DataFrame or None if unavailable.
    """
    import yfinance as yf

    if not feature_toggles.is_enabled("yahoo_finance"):
        return None
    if is_blocked():
        log.debug("market_cache.blocked", ticker=ticker)
        return None

    ttl_type = "intraday" if interval in ("1m","2m","5m","15m","30m","60m") else "daily"
    # Extra kwargs (e.g. explicit start/end dates) change what's actually being
    # asked for, so they need to be part of the key too — otherwise a call with
    # start="2024-01-01" and a call with start="2025-01-01" would collide on the
    # exact same cache entry just because period/interval happened to match.
    extra = "".join(f":{k}={v}" for k, v in sorted(kwargs.items()))
    key = f"download:{ticker}:{period}:{interval}{extra}"

    cached = _get(key)
    if cached is not None:
        import pandas as pd
        try:
            df = pd.read_json(json.dumps(cached))
            log.debug("market_cache.hit", ticker=ticker, key=key)
            return df
        except Exception:
            pass

    try:
        import pandas as pd
        df = yf.download(
            ticker,
            period=period,
            interval=interval,
            auto_adjust=True,
            progress=False,
            **kwargs
        )
        if df is not None and not df.empty:
            _set(key, json.loads(df.to_json()), _TTL[ttl_type])
            log.info("market_cache.fetched", ticker=ticker, rows=len(df))
            log_event("yahoo_finance", "info", f"Fetched {ticker} ({period}/{interval})", {"rows": len(df)})
            _note_fetch(f"Fetched {ticker} ({period}/{interval})")
        return df
    except Exception as exc:
        err = str(exc).lower()
        if "401" in err or "unauthorized" in err or "crumb" in err or "too many" in err:
            _trigger_backoff()
        log.warning("market_cache.fetch_error", ticker=ticker, error=str(exc))
        log_event("yahoo_finance", "error", f"Fetch failed for {ticker}", {"error": str(exc)})
        return None


def get_quote(symbol: str, exchange: str = "NSE") -> Optional[float]:
    """Get last traded price with 1-minute cache."""
    import yfinance as yf

    if not feature_toggles.is_enabled("yahoo_finance"):
        return None
    if is_blocked():
        return None

    suffix = ".NS" if exchange == "NSE" else ".BO"
    key = f"quote:{symbol}{suffix}"

    cached = _get(key)
    if cached is not None:
        log.debug("market_cache.quote_hit", symbol=symbol)
        return float(cached)

    try:
        ticker = yf.Ticker(f"{symbol}{suffix}")
        price  = ticker.fast_info.last_price
        if price:
            _set(key, float(price), _TTL["quote"])
            log_event("yahoo_finance", "info", f"Fetched quote {symbol}{suffix}", {"price": price})
            _note_fetch(f"Fetched quote {symbol}{suffix}")
            return float(price)
        log_event("yahoo_finance", "warning", f"No quote returned for {symbol}{suffix}")
        return None
    except Exception as exc:
        err = str(exc).lower()
        if "401" in err or "unauthorized" in err:
            _trigger_backoff()
        log_event("yahoo_finance", "error", f"Quote fetch failed for {symbol}{suffix}", {"error": str(exc)})
        return None


def get_info(symbol: str, exchange: str = "NSE") -> Optional[dict]:
    """Get company info with 1-hour cache."""
    import yfinance as yf

    if not feature_toggles.is_enabled("yahoo_finance"):
        return None
    if is_blocked():
        return None

    suffix = ".NS" if exchange == "NSE" else ".BO"
    key = f"info:{symbol}{suffix}"

    cached = _get(key)
    if cached is not None:
        return cached

    try:
        ticker = yf.Ticker(f"{symbol}{suffix}")
        info   = dict(ticker.info)
        _set(key, info, _TTL["info"])
        log_event("yahoo_finance", "info", f"Fetched company info {symbol}{suffix}")
        _note_fetch(f"Fetched company info {symbol}{suffix}")
        return info
    except Exception as exc:
        err = str(exc).lower()
        if "401" in err or "unauthorized" in err:
            _trigger_backoff()
        log_event("yahoo_finance", "error", f"Info fetch failed for {symbol}{suffix}", {"error": str(exc)})
        return None


def get_calendar(symbol: str, exchange: str = "NSE") -> Optional[dict]:
    """Earnings/dividend calendar, 24-hour cache."""
    import yfinance as yf

    if not feature_toggles.is_enabled("yahoo_finance"):
        return None
    if is_blocked():
        return None

    suffix = ".NS" if exchange == "NSE" else ".BO"
    key = f"calendar:{symbol}{suffix}"

    cached = _get(key)
    if cached is not None:
        return cached

    try:
        ticker = yf.Ticker(f"{symbol}{suffix}")
        cal = dict(ticker.calendar or {})
        _set(key, cal, _TTL["calendar"])
        log_event("yahoo_finance", "info", f"Fetched calendar {symbol}{suffix}")
        _note_fetch(f"Fetched calendar {symbol}{suffix}")
        return cal
    except Exception as exc:
        err = str(exc).lower()
        if "401" in err or "unauthorized" in err:
            _trigger_backoff()
        log_event("yahoo_finance", "error", f"Calendar fetch failed for {symbol}{suffix}", {"error": str(exc)})
        return None


def get_major_holders(symbol: str, exchange: str = "NSE") -> Optional[Any]:
    """Major shareholders breakdown, 24-hour cache. Returns a pandas DataFrame."""
    import yfinance as yf
    import pandas as pd

    if not feature_toggles.is_enabled("yahoo_finance"):
        return None
    if is_blocked():
        return None

    suffix = ".NS" if exchange == "NSE" else ".BO"
    key = f"holders:{symbol}{suffix}"

    cached = _get(key)
    if cached is not None:
        try:
            return pd.read_json(json.dumps(cached))
        except Exception:
            pass

    try:
        ticker = yf.Ticker(f"{symbol}{suffix}")
        df = ticker.major_holders
        if df is not None and not df.empty:
            _set(key, json.loads(df.to_json()), _TTL["holders"])
        log_event("yahoo_finance", "info", f"Fetched major holders {symbol}{suffix}")
        _note_fetch(f"Fetched major holders {symbol}{suffix}")
        return df
    except Exception as exc:
        err = str(exc).lower()
        if "401" in err or "unauthorized" in err:
            _trigger_backoff()
        log_event("yahoo_finance", "error", f"Major holders fetch failed for {symbol}{suffix}", {"error": str(exc)})
        return None


def get_insider_transactions(symbol: str, exchange: str = "NSE") -> Optional[Any]:
    """Insider transaction history, 24-hour cache. Returns a pandas DataFrame."""
    import yfinance as yf
    import pandas as pd

    if not feature_toggles.is_enabled("yahoo_finance"):
        return None
    if is_blocked():
        return None

    suffix = ".NS" if exchange == "NSE" else ".BO"
    key = f"insider:{symbol}{suffix}"

    cached = _get(key)
    if cached is not None:
        try:
            return pd.read_json(json.dumps(cached))
        except Exception:
            pass

    try:
        ticker = yf.Ticker(f"{symbol}{suffix}")
        df = ticker.insider_transactions
        if df is not None and not df.empty:
            _set(key, json.loads(df.to_json()), _TTL["holders"])
        log_event("yahoo_finance", "info", f"Fetched insider transactions {symbol}{suffix}")
        _note_fetch(f"Fetched insider transactions {symbol}{suffix}")
        return df
    except Exception as exc:
        err = str(exc).lower()
        if "401" in err or "unauthorized" in err:
            _trigger_backoff()
        log_event("yahoo_finance", "error", f"Insider transactions fetch failed for {symbol}{suffix}", {"error": str(exc)})
        return None


def get_news(symbol: str, exchange: str = "NSE", limit: int = 5) -> list[dict]:
    """Get recent news headlines for a symbol, 30-minute cache. Free, no API key."""
    import yfinance as yf

    if is_blocked():
        return []

    suffix = ".NS" if exchange == "NSE" else ".BO"
    key = f"news:{symbol}{suffix}"

    cached = _get(key)
    if cached is not None:
        return cached[:limit]

    try:
        ticker = yf.Ticker(f"{symbol}{suffix}")
        raw = ticker.news or []
        items = [
            {
                "title":   n.get("content", {}).get("title", ""),
                "summary": n.get("content", {}).get("summary", ""),
                "pub_date": n.get("content", {}).get("pubDate", ""),
                "link":    (n.get("content", {}).get("canonicalUrl", {}) or {}).get("url", "")
                           or (n.get("content", {}).get("clickThroughUrl", {}) or {}).get("url", ""),
            }
            for n in raw
        ]
        _set(key, items, _TTL["intraday"])
        headline_preview = f" — \"{items[0]['title'][:90]}\"" if items else ""
        log_event("news", "info", f"Fetched {len(items)} news items: {symbol}{suffix}{headline_preview}")
        _note_fetch(f"Fetched news for {symbol}{suffix}{headline_preview}")
        return items[:limit]
    except Exception as exc:
        err = str(exc).lower()
        if "401" in err or "unauthorized" in err:
            _trigger_backoff()
        log_event("news", "error", f"News fetch failed for {symbol}{suffix}", {"error": str(exc)})
        return []


def cache_stats() -> dict:
    """Return cache statistics."""
    try:
        conn  = _get_conn()
        total = conn.execute("SELECT COUNT(*) FROM market_cache").fetchone()[0]
        valid = conn.execute(
            "SELECT COUNT(*) FROM market_cache WHERE (? - fetched_at) <= ttl",
            (time.time(),)
        ).fetchone()[0]
        conn.close()
        size_bytes = _DB_PATH.stat().st_size if _DB_PATH.exists() else 0
        return {
            "total_entries": total,
            "valid_entries": valid,
            "expired":       total - valid,
            "blocked_until": datetime.fromtimestamp(_blocked_until).isoformat() if is_blocked() else None,
            "db_path":       str(_DB_PATH.absolute()),
            "size_mb":       round(size_bytes / (1024 * 1024), 2),
        }
    except Exception:
        return {}
