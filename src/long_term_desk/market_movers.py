"""
MarketMovers — pulls real, live top-gainer stocks from NSE/BSE, so the desk's
candidate pool isn't limited to the fixed config.toml universe. Fed into the
same scan pass as the static universe, not a separate side-channel.

Uses yfinance's screener (an unofficial Yahoo endpoint) — best-effort, never
allowed to break the scan loop if it's slow, rate-limited, or the endpoint
changes shape.
"""
from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

_MIN_PCT_CHANGE   = 5.0
_MIN_DAY_VOLUME   = 100_000
_MIN_MARKET_CAP   = 5_000_000_000  # ₹500 crore — excludes illiquid micro-caps
_MAX_RESULTS      = 15


def fetch_top_movers() -> list[str]:
    """Real top-gainer NSE symbols right now, deduped, no exchange suffix.
    Synchronous/blocking — call from an executor, not the event loop."""
    try:
        import yfinance as yf

        query = yf.EquityQuery("and", [
            yf.EquityQuery("eq", ["region", "in"]),
            yf.EquityQuery("gt", ["percentchange", _MIN_PCT_CHANGE]),
            yf.EquityQuery("gt", ["dayvolume", _MIN_DAY_VOLUME]),
            yf.EquityQuery("gt", ["intradaymarketcap", _MIN_MARKET_CAP]),
        ])
        result = yf.screen(query, count=_MAX_RESULTS * 2, sortField="percentchange", sortAsc=False)

        symbols: list[str] = []
        seen: set[str] = set()
        for quote in result.get("quotes", []):
            raw = quote.get("symbol", "")
            if not raw.endswith(".NS"):
                continue  # skip .BO duplicates — rest of the system is NSE-only
            symbol = raw[:-3]
            if symbol in seen:
                continue
            seen.add(symbol)
            symbols.append(symbol)
            if len(symbols) >= _MAX_RESULTS:
                break

        log.info("market_movers.fetched", count=len(symbols), symbols=symbols)
        return symbols
    except Exception as exc:
        log.warning("market_movers.fetch_failed", error=str(exc))
        return []
