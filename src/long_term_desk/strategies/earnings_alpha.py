"""EarningsAlphaAgent — expected vs implied move, beat/miss rate, guidance NLP."""
from __future__ import annotations

import asyncio
from typing import Optional

import structlog

from ...llm.llm_gateway import LLMGateway
from ...shared.market_data_cache import get_calendar, get_info
from ...shared.schemas import Exchange, SignalDirection, StrategySignal
from .base_strategy import BaseStrategy

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT = """You are an earnings specialist for Indian equities.
Given the earnings data, analyse if the stock is likely to move significantly post-earnings.
Respond ONLY in JSON:
{
  "direction": "long" | "short" | "neutral",
  "conviction": 0.0-1.0,
  "expected_move_pct": float,
  "rationale": "brief explanation"
}"""


class EarningsAlphaAgent(BaseStrategy):
    name = "earnings_alpha"
    default_expiry_hours = 24  # pre-earnings signal expires fast

    async def analyse(self, symbol: str, exchange: Exchange) -> Optional[StrategySignal]:
        data = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._fetch_earnings_data(symbol)
        )
        if data is None:
            return None

        try:
            llm    = LLMGateway.get()
            result = await llm.complete_json(
                agent_id="pod.strategy_agent",
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=f"Symbol: {symbol}\nEarnings Data: {data}",
            )
        except Exception:
            return None

        direction_str = result.get("direction", "neutral")
        conviction    = float(result.get("conviction", 0.0))

        if direction_str == "neutral" or conviction < 0.6:
            return None

        direction = SignalDirection.LONG if direction_str == "long" else SignalDirection.SHORT
        return StrategySignal(
            strategy_name=self.name,
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            conviction=conviction,
            timeframe="1d",
            rationale=result.get("rationale", ""),
            supporting_indicators={"expected_move_pct": result.get("expected_move_pct", 0)},
            expires_at=self._make_expiry(24),
        )

    @staticmethod
    def _fetch_earnings_data(symbol: str) -> Optional[dict]:
        try:
            info = get_info(symbol)
            if not info:
                return None
            cal = get_calendar(symbol) or {}
            return {
                "eps_actual":     info.get("trailingEps"),
                "eps_estimate":   info.get("epsForward"),
                "revenue_actual": info.get("totalRevenue"),
                "gross_margins":  info.get("grossMargins"),
                "earnings_growth": info.get("earningsGrowth"),
                "next_earnings":  str(cal.get("Earnings Date", [None])[0]),
            }
        except Exception:
            return None
