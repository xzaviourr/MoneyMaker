"""MomentumSurfAgent — Rate-of-change, relative strength, momentum factor, RRG."""
from __future__ import annotations

import asyncio
from typing import Optional

import numpy as np
import pandas as pd
import structlog

from ...shared.market_data_cache import download as _cached_download
from ...shared.schemas import Exchange, SignalDirection, StrategySignal
from .base_strategy import BaseStrategy

log = structlog.get_logger(__name__)


class MomentumSurfAgent(BaseStrategy):
    name = "momentum_surf"

    async def analyse(self, symbol: str, exchange: Exchange) -> Optional[StrategySignal]:
        df, nifty = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._fetch(symbol)
        )
        if df is None or len(df) < 126:
            return None

        close       = df["Close"].squeeze()
        nifty_close = nifty["Close"].squeeze() if nifty is not None else None

        # 6-month + 3-month price momentum
        mom_6m = float((close.iloc[-1] - close.iloc[-126]) / close.iloc[-126] * 100)
        mom_3m = float((close.iloc[-1] - close.iloc[-63]) / close.iloc[-63] * 100)

        # Relative strength vs Nifty50
        rs = 0.0
        if nifty_close is not None and len(nifty_close) >= 63:
            nifty_ret = float(
                (nifty_close.iloc[-1] - nifty_close.iloc[-63]) / nifty_close.iloc[-63] * 100
            )
            rs = mom_3m - nifty_ret

        if mom_6m < 10 and mom_3m < 5:
            return None  # no meaningful momentum

        direction  = SignalDirection.LONG if mom_6m > 0 else SignalDirection.SHORT
        conviction = min(0.82, 0.3 + (abs(mom_6m) / 100) * 0.4 + max(rs, 0) / 200)

        return StrategySignal(
            strategy_name=self.name,
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            conviction=conviction,
            timeframe="1w",
            rationale=f"6m_mom={mom_6m:.1f}%, 3m_mom={mom_3m:.1f}%, RS_vs_Nifty={rs:.1f}%",
            supporting_indicators={"mom_6m": mom_6m, "mom_3m": mom_3m, "rs_vs_nifty": rs},
            expires_at=self._make_expiry(120),
        )

    @staticmethod
    def _fetch(symbol: str):
        try:
            df    = _cached_download(f"{symbol}.NS", period="9mo", interval="1d")
            nifty = _cached_download("^NSEI", period="4mo", interval="1d")
            return df, nifty if nifty is not None and not nifty.empty else None
        except Exception:
            return None, None
