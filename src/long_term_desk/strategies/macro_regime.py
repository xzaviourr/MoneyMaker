"""MacroRegimeAgent — Yield curve, DXY, VIX, credit spreads, RBI posture. Can suppress strategy categories."""
from __future__ import annotations

import asyncio
from typing import Optional

import structlog

from ...shared.market_data_cache import download as _cached_download
from ...shared.schemas import Exchange, MarketBias, SignalDirection, StrategySignal
from .base_strategy import BaseStrategy

log = structlog.get_logger(__name__)


class MacroRegimeAgent(BaseStrategy):
    name = "macro_regime"
    default_expiry_hours = 168

    async def analyse(self, symbol: str, exchange: Exchange) -> Optional[StrategySignal]:
        data = await asyncio.get_event_loop().run_in_executor(None, self._fetch_macro)
        if not data:
            return None
        direction, conviction, rationale = self._interpret(data)
        if direction == SignalDirection.NEUTRAL or conviction < 0.5:
            return None
        return StrategySignal(
            strategy_name=self.name,
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            conviction=conviction,
            timeframe="1m",
            rationale=rationale,
            supporting_indicators=data,
            expires_at=self._make_expiry(168),
        )

    @staticmethod
    def _fetch_macro() -> dict:
        try:
            tickers = {"vix": "^INDIAVIX", "dxy": "DX-Y.NYB", "usdinr": "USDINR=X",
                       "nifty": "^NSEI", "us10y": "^TNX"}
            result = {}
            for key, ticker in tickers.items():
                df = _cached_download(ticker, period="5d", interval="1d")
                if df is not None and not df.empty:
                    result[key] = float(df["Close"].iloc[-1])
            return result
        except Exception:
            return {}

    @staticmethod
    def _interpret(data: dict) -> tuple[SignalDirection, float, str]:
        vix    = data.get("vix", 16)
        usdinr = data.get("usdinr", 83)
        notes  = []
        bull_score = 0

        if vix < 14:
            bull_score += 2
            notes.append(f"Low VIX {vix:.1f}")
        elif vix > 22:
            bull_score -= 2
            notes.append(f"High VIX {vix:.1f}")

        if usdinr < 83:
            bull_score += 1
            notes.append("INR stable")
        elif usdinr > 85:
            bull_score -= 1
            notes.append(f"INR weak USDINR={usdinr:.1f}")

        if bull_score >= 2:
            return SignalDirection.LONG, min(0.78, 0.4 + bull_score * 0.1), "; ".join(notes)
        if bull_score <= -2:
            return SignalDirection.SHORT, min(0.78, 0.4 + abs(bull_score) * 0.1), "; ".join(notes)
        return SignalDirection.NEUTRAL, 0.0, "No clear macro signal"
