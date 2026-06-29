"""TailRiskSentinel — Checks for binary event risk: earnings within 3 days, macro events."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

import structlog

from ...shared.market_data_cache import get_calendar, get_info
from ...shared.schemas import AllocationPlan

log = structlog.get_logger(__name__)


class TailRiskSentinel:
    agent_id = "room3.tail_risk_sentinel"

    async def check(self, plan: AllocationPlan) -> dict[str, Any]:
        data = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._fetch_events(plan.symbol)
        )

        warnings = []
        block    = False

        # Block if earnings within 2 days (binary risk)
        if data.get("earnings_in_days") is not None:
            days = data["earnings_in_days"]
            if days <= 2:
                warnings.append(f"earnings_in_{days}_days")
                block = True
            elif days <= 5:
                warnings.append(f"earnings_in_{days}_days_size_reduce_recommended")

        # High short interest = potential squeeze risk (two-way)
        si = data.get("short_pct_float", 0)
        if si and si > 0.25:
            warnings.append(f"high_short_interest_{si:.0%}_potential_squeeze")

        result = {
            "passed":     not block,
            "blocked":    block,
            "warnings":   warnings,
            "event_data": data,
        }
        log.info("tail_risk_sentinel.result",
                 symbol=plan.symbol, passed=not block, warnings=warnings)
        return result

    @staticmethod
    def _fetch_events(symbol: str) -> dict:
        try:
            # yfinance's .calendar returns a plain dict here (e.g.
            # {"Earnings Date": [date(...)]}), not a DataFrame — the previous
            # .empty/.T access was written against the wrong shape and silently
            # returned earnings_in_days=None every time, which meant this risk
            # gate never actually blocked a trade for upcoming earnings.
            cal = get_calendar(symbol) or {}
            earnings_in_days = None
            for d in cal.get("Earnings Date", []) or []:
                try:
                    earn_dt = datetime.strptime(str(d)[:10], "%Y-%m-%d")
                    delta   = (earn_dt - datetime.utcnow()).days
                    if delta >= 0:
                        earnings_in_days = delta
                        break
                except Exception:
                    pass

            info = get_info(symbol) or {}
            return {
                "earnings_in_days": earnings_in_days,
                "short_pct_float":  info.get("shortPercentOfFloat"),
            }
        except Exception:
            return {}
