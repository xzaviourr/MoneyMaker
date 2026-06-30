"""CatalystHunterAgent — Upcoming earnings, FDA/SEBI decisions, product launches, M&A rumours."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional

import structlog

from ...llm.llm_gateway import LLMGateway
from ...shared.market_data_cache import get_calendar, get_info
from ...shared.schemas import Exchange, SignalDirection, StrategySignal
from .base_strategy import BaseStrategy

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT = """You are a catalyst analyst for Indian equities.
Given the company profile, upcoming events, and news context, identify whether there is a
near-term binary catalyst that could drive significant price movement.
Respond ONLY in JSON:
{
  "has_catalyst": true|false,
  "direction": "long"|"short"|"neutral",
  "conviction": 0.0-1.0,
  "catalyst_type": "earnings"|"regulatory"|"ma"|"product"|"macro"|"other",
  "time_horizon_days": 1-30,
  "rationale": "brief explanation"
}
"""


class CatalystHunterAgent(BaseStrategy):
    name = "catalyst_hunter"

    async def analyse(self, symbol: str, exchange: Exchange) -> Optional[StrategySignal]:
        profile = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._fetch_profile(symbol)
        )
        if not profile:
            return None

        try:
            llm    = LLMGateway.get()
            result = await llm.complete_json(
                agent_id="lt_desk.catalyst_hunter",
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=(
                    f"Symbol: {symbol} ({exchange.value})\n"
                    f"Company Profile:\n{profile}"
                ),
            )
        except Exception:
            log.exception("catalyst_hunter.llm_error", symbol=symbol)
            return None

        if not result.get("has_catalyst", False):
            return None

        direction_str = result.get("direction", "neutral")
        conviction    = float(result.get("conviction", 0.0))

        if direction_str == "neutral" or conviction < 0.55:
            return None

        direction       = SignalDirection.LONG if direction_str == "long" else SignalDirection.SHORT
        horizon_days    = int(result.get("time_horizon_days", 7))
        catalyst_type   = result.get("catalyst_type", "other")

        return StrategySignal(
            strategy_name=self.name,
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            conviction=conviction,
            timeframe="1d",
            rationale=f"[{catalyst_type}] {result.get('rationale', '')}",
            supporting_indicators={
                "catalyst_type":       catalyst_type,
                "time_horizon_days":   horizon_days,
                "earnings_date":       profile.get("next_earnings"),
                "sector":              profile.get("sector"),
            },
            expires_at=self._make_expiry(horizon_days * 24),
        )

    @staticmethod
    def _fetch_profile(symbol: str) -> Optional[dict]:
        try:
            info = get_info(symbol)
            if not info:
                return None

            # Earnings calendar — yfinance returns a plain dict here, e.g.
            # {"Earnings Date": [date(...)], ...}, not a DataFrame.
            cal       = get_calendar(symbol) or {}
            next_earn = None
            dates = cal.get("Earnings Date")
            if dates:
                next_earn = str(dates[0])

            profile = {
                "name":            info.get("longName", symbol),
                "sector":          info.get("sector"),
                "industry":        info.get("industry"),
                "market_cap":      info.get("marketCap"),
                "next_earnings":   next_earn,
                "52w_change_pct":  info.get("52WeekChange"),
                "analyst_rec":     info.get("recommendationKey"),
                "target_mean":     info.get("targetMeanPrice"),
                "current_price":   info.get("currentPrice"),
                "revenue_growth":  info.get("revenueGrowth"),
                "earnings_growth": info.get("earningsGrowth"),
            }
            return {k: v for k, v in profile.items() if v is not None}
        except Exception:
            return None
