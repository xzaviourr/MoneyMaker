"""SentimentAgent — AAII (India proxy), put/call ratio, social media velocity, news NLP."""
from __future__ import annotations

import asyncio
from typing import Optional

import structlog

from ...llm.llm_gateway import LLMGateway
from ...shared.market_data_cache import get_info
from ...shared.schemas import Exchange, SignalDirection, StrategySignal
from .base_strategy import BaseStrategy

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT = """You are a contrarian sentiment analyst for Indian equities.
Given the sentiment data, determine the contrarian trade direction.
Contrarian: extreme bearish sentiment → LONG, extreme bullish sentiment → SHORT.
Respond ONLY in JSON:
{"direction": "long" | "short" | "neutral", "conviction": 0.0-1.0,
 "sentiment_extreme": "bearish" | "bullish" | "neutral", "rationale": "brief"}
"""


class SentimentAgent(BaseStrategy):
    name = "sentiment"

    async def analyse(self, symbol: str, exchange: Exchange) -> Optional[StrategySignal]:
        data = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._fetch_sentiment_proxies(symbol)
        )
        if not data:
            return None

        try:
            llm    = LLMGateway.get()
            result = await llm.complete_json(
                agent_id="pod.strategy_agent",
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=f"Symbol: {symbol}\nSentiment Data: {data}",
            )
        except Exception:
            return None

        direction_str = result.get("direction", "neutral")
        conviction    = float(result.get("conviction", 0.0))
        if direction_str == "neutral" or conviction < 0.55:
            return None

        direction = SignalDirection.LONG if direction_str == "long" else SignalDirection.SHORT
        return StrategySignal(
            strategy_name=self.name,
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            conviction=conviction,
            timeframe="1w",
            rationale=f"Contrarian: {result.get('rationale', '')}",
            supporting_indicators=data,
            expires_at=self._make_expiry(72),
        )

    @staticmethod
    def _fetch_sentiment_proxies(symbol: str) -> dict:
        try:
            info = get_info(symbol)
            if not info:
                return {}
            # Proxy metrics available via yfinance
            return {
                "short_percent_float": info.get("shortPercentOfFloat"),
                "days_to_cover":       info.get("shortRatio"),
                "analyst_target":      info.get("targetMeanPrice"),
                "analyst_rec":         info.get("recommendationKey"),
                "52w_high_dist_pct":   (
                    (info.get("currentPrice", 0) - info.get("fiftyTwoWeekHigh", 1))
                    / info.get("fiftyTwoWeekHigh", 1) * 100
                    if info.get("fiftyTwoWeekHigh") else None
                ),
                "52w_low_dist_pct":    (
                    (info.get("currentPrice", 0) - info.get("fiftyTwoWeekLow", 1))
                    / info.get("fiftyTwoWeekLow", 1) * 100
                    if info.get("fiftyTwoWeekLow") else None
                ),
            }
        except Exception:
            return {}
