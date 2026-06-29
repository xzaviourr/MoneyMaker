"""StatArbAgent — Cointegrated pairs, spread z-score, ETF vs constituent arbitrage."""
from __future__ import annotations

import asyncio
from typing import Optional

import numpy as np
import pandas as pd
import structlog
from statsmodels.tsa.stattools import coint

from ...shared.market_data_cache import download as _cached_download
from ...shared.schemas import Exchange, SignalDirection, StrategySignal
from .base_strategy import BaseStrategy

log = structlog.get_logger(__name__)

# Pre-defined cointegrated pairs for NSE
_KNOWN_PAIRS = [
    ("HDFCBANK", "ICICIBANK"),
    ("RELIANCE", "ONGC"),
    ("TCS", "INFY"),
    ("SBIN", "BANKBARODA"),
    ("MARUTI", "TATAMOTORS"),
]


class StatArbAgent(BaseStrategy):
    name = "stat_arb"

    async def analyse(self, symbol: str, exchange: Exchange) -> Optional[StrategySignal]:
        # Find if this symbol is part of a cointegrated pair
        pair = next((p for p in _KNOWN_PAIRS if symbol in p), None)
        if pair is None:
            return None

        other = pair[1] if pair[0] == symbol else pair[0]

        spread_data = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._compute_spread(symbol, other)
        )
        if spread_data is None:
            return None

        z_score = spread_data["z_score"]
        if abs(z_score) < 2.0:
            return None

        # Positive z: spread too wide → short symbol (it's overpriced relative to pair)
        # Negative z: spread too tight → long symbol (it's underpriced)
        direction  = SignalDirection.LONG if z_score < -2.0 else SignalDirection.SHORT
        conviction = min(0.80, 0.45 + (abs(z_score) - 2.0) * 0.1)

        return StrategySignal(
            strategy_name=self.name,
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            conviction=conviction,
            timeframe="1d",
            rationale=f"Stat arb pair ({symbol}/{other}): z={z_score:.2f}",
            supporting_indicators={**spread_data, "pair": f"{symbol}/{other}"},
            expires_at=self._make_expiry(48),
        )

    @staticmethod
    def _compute_spread(sym1: str, sym2: str) -> Optional[dict]:
        try:
            s1_df = _cached_download(f"{sym1}.NS", period="3mo", interval="1d")
            s2_df = _cached_download(f"{sym2}.NS", period="3mo", interval="1d")
            if s1_df is None or s2_df is None:
                return None
            s1 = s1_df["Close"].squeeze()
            s2 = s2_df["Close"].squeeze()
            if len(s1) < 30 or len(s2) < 30:
                return None
            # Align
            df = pd.concat([s1, s2], axis=1).dropna()
            df.columns = ["s1", "s2"]
            # Cointegration test
            score, p_value, _ = coint(df["s1"], df["s2"])
            if p_value > 0.05:
                return None  # not cointegrated

            # Compute hedge ratio via OLS
            beta = np.cov(df["s1"], df["s2"])[0, 1] / np.var(df["s2"])
            spread = df["s1"] - beta * df["s2"]
            z_score = float((spread.iloc[-1] - spread.mean()) / spread.std())
            return {
                "z_score": z_score,
                "beta":    beta,
                "p_value": p_value,
                "spread_mean": float(spread.mean()),
                "spread_std":  float(spread.std()),
            }
        except Exception:
            return None
