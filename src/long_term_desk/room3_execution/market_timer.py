"""MarketTimer — Determines optimal execution timing: VWAP window, market depth, session timing."""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timezone, timedelta
from typing import Any

import structlog

from ...shared.market_data_cache import download as _cached_download
from ...shared.market_hours import is_market_open
from ...shared.schemas import AllocationPlan

_IST = timezone(timedelta(hours=5, minutes=30))

log = structlog.get_logger(__name__)

# NSE optimal execution windows (IST)
_OPEN_AVOID_MINUTES  = 15   # Avoid opening 15 minutes (price discovery noise)
_CLOSE_AVOID_MINUTES = 30   # Avoid last 30 minutes (MOC effects)


class MarketTimer:
    agent_id = "room3.market_timer"

    async def advise(self, plan: AllocationPlan) -> dict[str, Any]:
        liquidity = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._get_liquidity(plan.symbol)
        )

        # datetime.utcnow() is plain UTC, not IST despite the variable name —
        # comparing it directly against IST clock boundaries meant the
        # "closed" window was actually checked against the wrong 5.5-hour
        # offset, and there was no check at all for "market not open today
        # in the first place" (after close, before open, weekends). That's
        # how a long-term-desk order got placed and filled at 5:18pm IST,
        # nearly two hours after the exchange closed.
        now_ist       = datetime.now(_IST)
        current_time  = now_ist.time()

        avoid_open  = time(9, 15) <= current_time <= time(9, 30)
        avoid_close = time(15, 0) <= current_time <= time(15, 30)

        # Slippage estimate based on average daily volume
        adv    = liquidity.get("avg_daily_volume", 0)
        qty    = plan.quantity
        impact = 0.0
        if adv > 0:
            impact_pct = (qty / adv) * 100  # % of ADV
            impact     = min(impact_pct * 0.05, 2.0)  # 5bps per 1% of ADV, capped at 200bps

        if not is_market_open(now_ist):
            advice = "wait_tomorrow_open"
            reason = "market_closed"
        elif avoid_open:
            advice = "wait_30min"
            reason = "opening_volatility_window"
        elif avoid_close:
            advice = "wait_tomorrow_open"
            reason = "closing_auction_risk"
        elif impact > 0.5:
            advice = "slice_over_2sessions"
            reason = f"large_impact_estimate_{impact:.1f}bps"
        else:
            advice = "immediate"
            reason = "normal_liquidity"

        result = {
            "execution_advice": advice,
            "reason":           reason,
            "estimated_impact_bps": impact * 100,
            "pct_of_adv":       (qty / adv * 100) if adv > 0 else 0,
            "liquidity":        liquidity,
        }
        log.info("market_timer.advice", symbol=plan.symbol,
                 advice=advice, impact_bps=impact * 100)
        return result

    @staticmethod
    def _get_liquidity(symbol: str) -> dict:
        try:
            df = _cached_download(f"{symbol}.NS", period="20d", interval="1d")
            if df is None or df.empty:
                return {}
            vol = df["Volume"].squeeze().astype(float)
            return {
                "avg_daily_volume": float(vol.mean()),
                "min_volume_20d":   float(vol.min()),
                "vol_consistency":  float(vol.std() / vol.mean()) if float(vol.mean()) > 0 else 0,
            }
        except Exception:
            return {}
