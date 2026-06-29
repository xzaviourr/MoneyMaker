"""BreakoutAgent — 52-week highs, volume-surge breakouts, consolidation range breaks."""
from __future__ import annotations

import asyncio
from typing import Optional

import numpy as np
import structlog

from ...shared.market_data_cache import download as _cached_download
from ...shared.schemas import Exchange, SignalDirection, StrategySignal
from .base_strategy import BaseStrategy

log = structlog.get_logger(__name__)


class BreakoutAgent(BaseStrategy):
    name = "breakout_lt"

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
            timeframe="1d",
            rationale=data["rationale"],
            supporting_indicators=data["indicators"],
            expires_at=self._make_expiry(72),
        )

    @staticmethod
    def _compute(symbol: str) -> Optional[dict]:
        try:
            df = _cached_download(f"{symbol}.NS", period="1y", interval="1d")
            if df is None or len(df) < 60:
                return None

            close  = df["Close"].squeeze()
            volume = df["Volume"].squeeze()
            high   = df["High"].squeeze()
            low    = df["Low"].squeeze()

            current_close  = float(close.iloc[-1])
            w52_high       = float(high.iloc[-252:].max()) if len(high) >= 252 else float(high.max())
            w52_low        = float(low.iloc[-252:].min()) if len(low) >= 252 else float(low.min())
            avg_vol_20     = float(volume.iloc[-21:-1].mean())
            today_vol      = float(volume.iloc[-1])
            vol_multiple   = today_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0

            # 20-bar consolidation range
            range_high = float(high.iloc[-21:-1].max())
            range_low  = float(low.iloc[-21:-1].min())
            range_pct  = (range_high - range_low) / range_low if range_low > 0 else 1.0

            breakout_up   = current_close >= range_high * 1.005 and vol_multiple >= 1.5
            near_52w_high = current_close >= w52_high * 0.99

            breakdown_dn  = current_close <= range_low * 0.995 and vol_multiple >= 1.5
            near_52w_low  = current_close <= w52_low * 1.01

            if not (breakout_up or breakdown_dn):
                return None

            direction  = SignalDirection.LONG if breakout_up else SignalDirection.SHORT
            base_conv  = 0.55
            if near_52w_high and breakout_up:
                base_conv += 0.15
            if near_52w_low and breakdown_dn:
                base_conv += 0.15
            # Volume quality
            base_conv = min(0.85, base_conv + min(0.10, (vol_multiple - 1.5) * 0.05))

            if range_pct > 0.15:
                base_conv = max(0.50, base_conv - 0.05)  # wide range → less clean breakout

            return {
                "direction":  direction,
                "conviction": base_conv,
                "rationale":  (
                    f"Breakout {'UP' if breakout_up else 'DOWN'} from 20-bar range "
                    f"(vol x{vol_multiple:.1f}, 52w_high dist={(current_close/w52_high-1)*100:.1f}%)"
                ),
                "indicators": {
                    "52w_high": w52_high,
                    "52w_low":  w52_low,
                    "range_pct": range_pct,
                    "vol_multiple": vol_multiple,
                    "current_close": current_close,
                },
            }
        except Exception:
            log.exception("breakout_lt.compute_error", symbol=symbol)
            return None
