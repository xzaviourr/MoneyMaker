"""ShortInterestAgent — Short squeeze setup, days-to-cover, short interest vs float."""
from __future__ import annotations

import asyncio
from typing import Optional

import structlog

from ...shared.market_data_cache import download as cache_download, get_info as cache_get_info
from ...shared.schemas import Exchange, SignalDirection, StrategySignal
from .base_strategy import BaseStrategy

log = structlog.get_logger(__name__)


class ShortInterestAgent(BaseStrategy):
    name = "short_interest"

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
            expires_at=self._make_expiry(96),  # 4 days
        )

    @staticmethod
    def _compute(symbol: str) -> Optional[dict]:
        try:
            info = cache_get_info(symbol)
            if info is None:
                return None

            short_pct_float = info.get("shortPercentOfFloat")  # fraction 0-1
            days_to_cover   = info.get("shortRatio")            # float
            shares_short    = info.get("sharesShort", 0)
            float_shares    = info.get("floatShares", 0)

            if short_pct_float is None and days_to_cover is None:
                return None

            spf = float(short_pct_float or 0)
            dtc = float(days_to_cover or 0)

            # Short squeeze setup: high short interest + upward price pressure
            df = cache_download(f"{symbol}.NS", period="20d", interval="1d")
            if df is None or len(df) < 10:
                return None

            close  = df["Close"].squeeze().astype(float)
            volume = df["Volume"].squeeze().astype(float)
            price_trend_5d = (float(close.iloc[-1]) - float(close.iloc[-5])) / float(close.iloc[-5])

            # Squeeze setup: ≥15% of float short + rising price + high volume
            avg_vol_20  = float(volume.iloc[:-1].mean())
            recent_vol  = float(volume.iloc[-3:].mean())
            vol_ratio   = recent_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0

            squeeze_setup = spf >= 0.15 and price_trend_5d > 0.01 and vol_ratio >= 1.3
            # Heavy shorting with downtrend — momentum short
            bear_setup    = spf >= 0.20 and price_trend_5d < -0.01

            if not (squeeze_setup or bear_setup):
                return None

            direction = SignalDirection.LONG if squeeze_setup else SignalDirection.SHORT
            base_conv = 0.50

            if squeeze_setup:
                base_conv += min(0.20, (spf - 0.15) * 1.5)  # higher short % → more potential squeeze
                if dtc >= 5:
                    base_conv += 0.05  # 5+ days to cover → strong squeeze potential
                if vol_ratio >= 2.0:
                    base_conv += 0.05  # huge volume acceleration
            else:
                base_conv += min(0.18, (spf - 0.20) * 1.0)

            conviction = min(0.80, base_conv)

            return {
                "direction":  direction,
                "conviction": conviction,
                "rationale":  (
                    f"{'Squeeze setup' if squeeze_setup else 'Bear momentum'}: "
                    f"short_float={spf:.1%}, DTC={dtc:.1f}, "
                    f"price_5d={price_trend_5d:.1%}, vol_ratio={vol_ratio:.1f}x"
                ),
                "indicators": {
                    "short_pct_float":   spf,
                    "days_to_cover":     dtc,
                    "price_trend_5d":    price_trend_5d,
                    "volume_ratio":      vol_ratio,
                    "shares_short":      shares_short,
                },
            }
        except Exception:
            log.exception("short_interest.compute_error", symbol=symbol)
            return None
