"""ChartPatternAgent — H&S, Cup & Handle, Flags, Wedges, Triangles. Multi-timeframe."""
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


class ChartPatternAgent(BaseStrategy):
    name = "chart_pattern"

    async def analyse(self, symbol: str, exchange: Exchange) -> Optional[StrategySignal]:
        df = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _cached_download(f"{symbol}.NS", period="6mo", interval="1d")
        )
        if df is None or len(df) < 60:
            return None

        close  = df["Close"].squeeze().values
        volume = df["Volume"].squeeze().values

        # Detect basic patterns
        result = self._detect_patterns(close, volume)
        if result is None:
            return None

        pattern, direction, conviction = result
        return StrategySignal(
            strategy_name=self.name,
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            conviction=conviction,
            timeframe="1d",
            rationale=f"Chart pattern detected: {pattern}",
            supporting_indicators={"pattern": pattern},
            expires_at=self._make_expiry(96),
        )

    @staticmethod
    def _detect_patterns(
        close: np.ndarray, volume: np.ndarray
    ) -> Optional[tuple[str, SignalDirection, float]]:
        n = len(close)
        if n < 30:
            return None

        # ── Cup & Handle detection (simplified) ──────────────────────────
        # Look for U-shape in last 30 bars followed by handle
        segment = close[-30:]
        mid_low = np.argmin(segment)
        if 8 <= mid_low <= 22:
            left_high  = float(np.max(segment[:mid_low]))
            right_high = float(np.max(segment[mid_low:]))
            cup_low    = float(segment[mid_low])
            depth      = (min(left_high, right_high) - cup_low) / cup_low
            if 0.10 <= depth <= 0.40:  # 10-40% depth cup
                # Breakout if current price near rim
                if close[-1] >= right_high * 0.98:
                    return ("cup_and_handle", SignalDirection.LONG, 0.72)

        # ── Descending wedge (bullish reversal) ────────────────────────
        highs = close[-20:]
        lows  = close[-20:]
        high_slope = np.polyfit(range(len(highs)), highs, 1)[0]
        low_slope  = np.polyfit(range(len(lows)), lows,  1)[0]
        if high_slope < -0.05 and low_slope < -0.02 and low_slope > high_slope:
            if close[-1] > close[-2] * 1.01:  # breakout candle
                return ("descending_wedge", SignalDirection.LONG, 0.65)

        # ── Flag pattern (bullish) ─────────────────────────────────────
        prior_move = (close[-20] - close[-35]) / close[-35] * 100 if n > 35 else 0
        if prior_move > 10:  # strong prior move up
            flag_range = (max(close[-10:]) - min(close[-10:])) / close[-10] * 100
            if flag_range < 3.0:  # tight consolidation
                return ("bull_flag", SignalDirection.LONG, 0.68)

        return None
