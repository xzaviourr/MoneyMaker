"""VolumeProfileAgent — Value Area High/Low, Point of Control, VPOC migration, volume nodes."""
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


class VolumeProfileAgent(BaseStrategy):
    name = "volume_profile"

    async def analyse(self, symbol: str, exchange: Exchange) -> Optional[StrategySignal]:
        data = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._compute(symbol)
        )
        if data is None:
            return None

        direction  = data["direction"]
        conviction = data["conviction"]
        if conviction < 0.48:
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
            expires_at=self._make_expiry(48),
        )

    @staticmethod
    def _compute(symbol: str) -> Optional[dict]:
        try:
            df = _cached_download(f"{symbol}.NS", period="6mo", interval="1d")
            if df is None or len(df) < 60:
                return None

            close  = df["Close"].squeeze().astype(float)
            high   = df["High"].squeeze().astype(float)
            low    = df["Low"].squeeze().astype(float)
            volume = df["Volume"].squeeze().astype(float)

            # Build volume profile with 50 price buckets
            price_min  = float(low.min())
            price_max  = float(high.max())
            n_buckets  = 50
            bins       = np.linspace(price_min, price_max, n_buckets + 1)
            bin_centers = (bins[:-1] + bins[1:]) / 2

            vol_per_bucket = np.zeros(n_buckets)
            for i in range(len(df)):
                # Distribute day's volume proportionally across the day's range
                day_low  = float(low.iloc[i])
                day_high = float(high.iloc[i])
                day_vol  = float(volume.iloc[i])
                if day_high == day_low:
                    idx = np.searchsorted(bins[1:], float(close.iloc[i]))
                    idx = min(idx, n_buckets - 1)
                    vol_per_bucket[idx] += day_vol
                    continue
                for j in range(n_buckets):
                    overlap = max(0, min(bins[j+1], day_high) - max(bins[j], day_low))
                    frac    = overlap / (day_high - day_low)
                    vol_per_bucket[j] += day_vol * frac

            # Point of Control (highest volume bucket)
            poc_idx    = int(np.argmax(vol_per_bucket))
            poc_price  = float(bin_centers[poc_idx])

            # Value Area (70% of total volume)
            total_vol = vol_per_bucket.sum()
            sorted_idx = np.argsort(vol_per_bucket)[::-1]
            cum_vol    = 0.0
            va_idx     = []
            for idx in sorted_idx:
                cum_vol += vol_per_bucket[idx]
                va_idx.append(idx)
                if cum_vol >= 0.70 * total_vol:
                    break
            va_high = float(bin_centers[max(va_idx)])
            va_low  = float(bin_centers[min(va_idx)])

            cur = float(close.iloc[-1])

            # Low-volume node below current price → strong support (bullish)
            below_cur = [(bin_centers[i], vol_per_bucket[i]) for i in range(n_buckets)
                         if bin_centers[i] < cur]
            if not below_cur:
                return None

            # Find nearest support node (low-vol zone)
            sorted_below = sorted(below_cur, key=lambda x: abs(cur - x[0]))[:10]
            min_vol_near = min(sorted_below, key=lambda x: x[1])
            support_gap  = (cur - min_vol_near[0]) / cur * 100

            at_poc = abs(cur - poc_price) / poc_price < 0.01
            above_va = cur > va_high
            below_va = cur < va_low

            if above_va and at_poc:
                direction  = SignalDirection.LONG
                conviction = 0.62
                rationale  = f"Price above VA, at POC ({poc_price:.1f}), strong support"
            elif cur > va_low and cur < va_high:
                # Inside value area, trend with POC
                direction  = SignalDirection.LONG if cur > poc_price else SignalDirection.SHORT
                conviction = 0.52
                rationale  = f"Inside VA, {'above' if cur>poc_price else 'below'} POC={poc_price:.1f}"
            elif below_va:
                direction  = SignalDirection.SHORT
                conviction = 0.55
                rationale  = f"Price below VA_Low={va_low:.1f}, bearish volume structure"
            else:
                return None

            # Boost if near low-volume node support
            if support_gap < 2.0:
                conviction = min(0.78, conviction + 0.08)

            return {
                "direction":  direction,
                "conviction": conviction,
                "rationale":  rationale,
                "indicators": {
                    "poc":      poc_price,
                    "va_high":  va_high,
                    "va_low":   va_low,
                    "current":  cur,
                    "support_gap_pct": support_gap,
                },
            }
        except Exception:
            log.exception("volume_profile.compute_error", symbol=symbol)
            return None
