"""TrendFollowingAgent — EMA crossovers, ADX strength, Donchian breakouts."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import structlog

from ...shared.market_data_cache import download as _cached_download
from ...shared.schemas import Exchange, SignalDirection, StrategySignal
from .base_strategy import BaseStrategy

log = structlog.get_logger(__name__)


class TrendFollowingAgent(BaseStrategy):
    name = "trend_following"

    def __init__(self, fast: int = 20, slow: int = 50, adx_period: int = 14) -> None:
        super().__init__()
        self._fast = fast
        self._slow = slow
        self._adx_period = adx_period

    async def analyse(self, symbol: str, exchange: Exchange) -> Optional[StrategySignal]:
        import asyncio
        df = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._fetch(symbol)
        )
        if df is None or len(df) < self._slow + self._adx_period:
            return None

        close  = df["Close"].squeeze()
        ema20  = close.ewm(span=self._fast, adjust=False).mean()
        ema50  = close.ewm(span=self._slow, adjust=False).mean()
        adx    = self._compute_adx(df, self._adx_period)

        curr_fast, curr_slow = float(ema20.iloc[-1]), float(ema50.iloc[-1])
        prev_fast, prev_slow = float(ema20.iloc[-2]), float(ema50.iloc[-2])
        curr_adx = float(adx.iloc[-1]) if not adx.empty else 0.0

        if curr_adx < 20:
            return None  # no trend

        cross_up   = prev_fast <= prev_slow and curr_fast > curr_slow
        cross_down = prev_fast >= prev_slow and curr_fast < curr_slow

        if not (cross_up or cross_down):
            return None

        direction  = SignalDirection.LONG if cross_up else SignalDirection.SHORT
        conviction = min(0.85, 0.4 + curr_adx / 100 * 0.8)

        return StrategySignal(
            strategy_name=self.name,
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            conviction=conviction,
            timeframe="1d",
            rationale=f"EMA{self._fast}/EMA{self._slow} {'bull' if cross_up else 'bear'} cross, ADX={curr_adx:.1f}",
            supporting_indicators={"ema_fast": curr_fast, "ema_slow": curr_slow, "adx": curr_adx},
            expires_at=self._make_expiry(72),
        )

    @staticmethod
    def _fetch(symbol: str) -> Optional[pd.DataFrame]:
        try:
            return _cached_download(f"{symbol}.NS", period="6mo", interval="1d")
        except Exception:
            return None

    @staticmethod
    def _compute_adx(df: pd.DataFrame, period: int) -> pd.Series:
        high  = df["High"].squeeze()
        low   = df["Low"].squeeze()
        close = df["Close"].squeeze()
        tr = pd.concat([
            (high - low),
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr   = tr.rolling(period).mean()
        dm_p  = (high.diff()).clip(lower=0)
        dm_n  = (-low.diff()).clip(lower=0)
        dm_p  = dm_p.where(dm_p > dm_n, 0)
        dm_n  = dm_n.where(dm_n > dm_p, 0)
        di_p  = 100 * dm_p.rolling(period).mean() / atr.replace(0, np.nan)
        di_n  = 100 * dm_n.rolling(period).mean() / atr.replace(0, np.nan)
        dx    = 100 * ((di_p - di_n).abs() / (di_p + di_n).replace(0, np.nan))
        return dx.rolling(period).mean()
