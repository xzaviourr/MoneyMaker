"""MeanReversionLTAgent — Stretched valuations, Bollinger Band extremes, RSI divergence on weekly."""
from __future__ import annotations

import asyncio
from typing import Optional

import numpy as np
import structlog

from ...shared.market_data_cache import download as _cached_download
from ...shared.schemas import Exchange, SignalDirection, StrategySignal
from .base_strategy import BaseStrategy

log = structlog.get_logger(__name__)


class MeanReversionLTAgent(BaseStrategy):
    name = "mean_reversion_lt"

    async def analyse(self, symbol: str, exchange: Exchange) -> Optional[StrategySignal]:
        data = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._compute(symbol)
        )
        if data is None:
            return None

        direction  = data["direction"]
        conviction = data["conviction"]
        if conviction < 0.50:
            return None

        return StrategySignal(
            strategy_name=self.name,
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            conviction=conviction,
            timeframe="1wk",
            rationale=data["rationale"],
            supporting_indicators=data["indicators"],
            expires_at=self._make_expiry(168),  # 7 days
        )

    @staticmethod
    def _compute(symbol: str) -> Optional[dict]:
        try:
            # Weekly data for long-term mean reversion
            df = _cached_download(f"{symbol}.NS", period="2y", interval="1wk")
            if df is None or len(df) < 52:
                return None

            close = df["Close"].squeeze().astype(float)

            # Bollinger Bands (20-week, 2σ)
            sma20   = close.rolling(20).mean()
            std20   = close.rolling(20).std()
            upper   = sma20 + 2 * std20
            lower   = sma20 - 2 * std20

            cur = float(close.iloc[-1])
            band_upper = float(upper.iloc[-1])
            band_lower = float(lower.iloc[-1])
            band_mid   = float(sma20.iloc[-1])
            band_width = band_upper - band_lower

            pct_b = (cur - band_lower) / band_width if band_width > 0 else 0.5

            # RSI (14-week)
            delta = close.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            rs    = gain / loss.replace(0, np.nan)
            rsi   = float((100 - 100 / (1 + rs)).iloc[-1])

            # Distance from 52-week mean
            mean_52 = float(close.iloc[-52:].mean())
            dev_pct  = (cur - mean_52) / mean_52 * 100

            oversold  = pct_b < 0.05 and rsi < 35 and dev_pct < -20
            overbought = pct_b > 0.95 and rsi > 70 and dev_pct > 20

            if not (oversold or overbought):
                return None

            direction = SignalDirection.LONG if oversold else SignalDirection.SHORT
            # Conviction from severity of deviation
            extreme = abs(dev_pct) / 20  # 20% dev = 1.0
            base    = 0.50 + min(0.25, extreme * 0.12)
            if (oversold and rsi < 25) or (overbought and rsi > 80):
                base += 0.05
            if pct_b < 0.02 or pct_b > 0.98:
                base += 0.05
            conviction = min(0.82, base)

            return {
                "direction":  direction,
                "conviction": conviction,
                "rationale":  (
                    f"Weekly MR: %B={pct_b:.2f}, RSI={rsi:.1f}, "
                    f"52w dev={dev_pct:.1f}%, signal={'oversold' if oversold else 'overbought'}"
                ),
                "indicators": {
                    "pct_b": pct_b,
                    "rsi_weekly": rsi,
                    "dev_from_52w_mean_pct": dev_pct,
                    "band_upper": band_upper,
                    "band_lower": band_lower,
                    "band_mid": band_mid,
                },
            }
        except Exception:
            log.exception("mean_reversion_lt.compute_error", symbol=symbol)
            return None
