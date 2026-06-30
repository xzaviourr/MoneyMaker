"""OrderFlowAgent — FII/DII net buying, block deals, institutional accumulation signals."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional

import structlog

from ...shared.market_data_cache import (
    download as cache_download,
    get_info as cache_get_info,
    get_major_holders as cache_get_major_holders,
)
from ...shared.schemas import Exchange, SignalDirection, StrategySignal
from .base_strategy import BaseStrategy

log = structlog.get_logger(__name__)


class OrderFlowAgent(BaseStrategy):
    name = "order_flow"
    default_expiry_hours = 120  # 5 days

    async def analyse(self, symbol: str, exchange: Exchange) -> Optional[StrategySignal]:
        data = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._compute(symbol)
        )
        if data is None:
            return None

        direction  = data["direction"]
        conviction = data["conviction"]
        if conviction < 0.45:
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
            expires_at=self._make_expiry(120),
        )

    @staticmethod
    def _compute(symbol: str) -> Optional[dict]:
        try:
            info = cache_get_info(symbol)
            if info is None:
                return None

            # Institutional holding change (proxy for FII/DII)
            inst_hold_pct = info.get("institutionPercentHeld")
            fund_hold_pct = info.get("heldPercentInstitutions")

            # Guarded individually so a failure here only loses this one signal
            # input, not the whole strategy.
            inst_own_pct = 0.0
            try:
                major = cache_get_major_holders(symbol)
                if major is not None and not major.empty:
                    row = major[major.iloc[:, 1].str.contains("Institution", na=False)]
                    if not row.empty:
                        inst_own_pct = float(str(row.iloc[0, 0]).rstrip("%")) / 100
            except Exception:
                pass

            # Net buying approximation from price+volume divergence
            df = cache_download(f"{symbol}.NS", period="20d", interval="1d")
            if df is None or len(df) < 10:
                return None

            close  = df["Close"].squeeze().astype(float)
            volume = df["Volume"].squeeze().astype(float)

            # On-Balance Volume (OBV)
            obv = [0.0]
            for i in range(1, len(close)):
                if close.iloc[i] > close.iloc[i-1]:
                    obv.append(obv[-1] + float(volume.iloc[i]))
                elif close.iloc[i] < close.iloc[i-1]:
                    obv.append(obv[-1] - float(volume.iloc[i]))
                else:
                    obv.append(obv[-1])

            # OBV trend (last 10 bars)
            obv_recent   = obv[-10:]
            obv_slope    = (obv_recent[-1] - obv_recent[0]) / max(abs(obv_recent[0]), 1)
            price_change = (float(close.iloc[-1]) - float(close.iloc[-10])) / float(close.iloc[-10])

            # Accumulation: OBV rising faster than price (institutions buying dips)
            accumulation = obv_slope > 0.02 and obv_slope > price_change * 1.5
            distribution = obv_slope < -0.02 and obv_slope < price_change * 1.5

            if not (accumulation or distribution):
                return None

            direction = SignalDirection.LONG if accumulation else SignalDirection.SHORT
            base_conv = 0.50
            # High institutional ownership + accumulation → higher conviction
            if inst_own_pct > 0.40 and accumulation:
                base_conv += 0.10
            if inst_own_pct > 0.40 and distribution:
                base_conv += 0.08
            # Strong OBV slope
            base_conv = min(0.75, base_conv + min(0.12, abs(obv_slope) * 2))

            return {
                "direction":  direction,
                "conviction": base_conv,
                "rationale":  (
                    f"{'Accumulation' if accumulation else 'Distribution'}: "
                    f"OBV_slope={obv_slope:.3f}, price_chg={price_change:.3f}"
                ),
                "indicators": {
                    "obv_slope_10d":   obv_slope,
                    "price_change_10d": price_change,
                    "inst_ownership_pct": inst_own_pct,
                    "obv_current":    obv[-1],
                },
            }
        except Exception:
            log.exception("order_flow.compute_error", symbol=symbol)
            return None
