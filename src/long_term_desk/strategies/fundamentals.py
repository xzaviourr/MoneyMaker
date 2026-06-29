"""
FundamentalsAgent — P/E vs sector, PEG, FCF yield, ROIC, debt-to-equity.
Long-term only (weeks–months horizon).
"""
from __future__ import annotations

import asyncio
from typing import Optional

import structlog

from ...shared.market_data_cache import get_info
from ...shared.schemas import Exchange, SignalDirection, StrategySignal
from .base_strategy import BaseStrategy

log = structlog.get_logger(__name__)

# Approximate sector median P/E for NSE (update quarterly)
_SECTOR_PE: dict[str, float] = {
    "Technology":           22.0,
    "Financial Services":   14.0,
    "Consumer Defensive":   38.0,
    "Healthcare":           24.0,
    "Energy":               12.0,
    "Industrials":          26.0,
    "Consumer Cyclical":    30.0,
    "Communication Services": 18.0,
    "Basic Materials":       15.0,
    "Utilities":             16.0,
    "Real Estate":           20.0,
}


class FundamentalsAgent(BaseStrategy):
    name = "fundamentals"
    default_expiry_hours = 168  # 1 week

    async def analyse(self, symbol: str, exchange: Exchange) -> Optional[StrategySignal]:
        data = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._fetch_fundamentals(symbol)
        )
        if data is None:
            return None

        score, rationale = self._score_fundamentals(data)

        if abs(score) < 2:
            return None  # not strong enough

        direction  = SignalDirection.LONG if score > 0 else SignalDirection.SHORT
        conviction = min(0.82, 0.35 + abs(score) * 0.1)

        return StrategySignal(
            strategy_name=self.name,
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            conviction=conviction,
            timeframe="1w",
            rationale=rationale,
            supporting_indicators=data,
            expires_at=self._make_expiry(),
        )

    @staticmethod
    def _fetch_fundamentals(symbol: str) -> Optional[dict]:
        try:
            info = get_info(symbol)
            if not info:
                return None
            return {
                "pe":          info.get("trailingPE"),
                "forward_pe":  info.get("forwardPE"),
                "peg":         info.get("pegRatio"),
                "fcf_yield":   info.get("freeCashflow"),
                "roic":        info.get("returnOnEquity"),
                "debt_equity": info.get("debtToEquity"),
                "sector":      info.get("sector", ""),
                "revenue_growth": info.get("revenueGrowth"),
                "market_cap":  info.get("marketCap"),
            }
        except Exception:
            return None

    @staticmethod
    def _score_fundamentals(d: dict) -> tuple[float, str]:
        score = 0.0
        notes = []

        # P/E vs sector
        pe     = d.get("pe")
        sector = d.get("sector", "")
        if pe and pe > 0:
            sector_pe = _SECTOR_PE.get(sector, 20.0)
            if pe < sector_pe * 0.8:
                score += 2
                notes.append(f"P/E {pe:.1f} < sector median {sector_pe:.1f}")
            elif pe > sector_pe * 1.3:
                score -= 1
                notes.append(f"P/E {pe:.1f} premium to sector")

        # PEG
        peg = d.get("peg")
        if peg and 0 < peg < 1:
            score += 2
            notes.append(f"PEG={peg:.2f} (attractive)")
        elif peg and peg > 2:
            score -= 1

        # ROIC (proxy: return on equity)
        roic = d.get("roic")
        if roic and roic > 0.18:
            score += 1
            notes.append(f"High ROIC {roic:.1%}")
        elif roic and roic < 0.05:
            score -= 1

        # Debt/equity
        de = d.get("debt_equity")
        if de is not None:
            if de < 30:
                score += 1
                notes.append("Low leverage")
            elif de > 150:
                score -= 2
                notes.append(f"High leverage D/E={de:.0f}%")

        # Revenue growth
        rev_growth = d.get("revenue_growth")
        if rev_growth and rev_growth > 0.15:
            score += 1
            notes.append(f"Rev growth {rev_growth:.1%}")
        elif rev_growth and rev_growth < -0.1:
            score -= 1

        return score, "; ".join(notes) or "No significant fundamental signal"
