"""
BreakoutPod — intraday opening-range breakout strategy.

Signal: price breaks above/below the high/low of the first few ticks
        with volume confirmation. Compatible with TRENDING regimes.
"""
from __future__ import annotations

from collections import deque
from decimal import Decimal
from typing import Optional

import structlog

from ..shared.schemas import (
    MarketRegimeTrend,
    PodConfig,
    PodState,
    Quote,
    SignalDirection,
    SignalStrength,
    TradeSignal,
)
from .base_pod import BasePod

log = structlog.get_logger(__name__)

_WATCHLIST: list[tuple[str, str]] = [
    ("WIPRO",     "NSE"),
    ("HCLTECH",   "NSE"),
    ("BAJFINANCE","NSE"),
    ("KOTAKBANK", "NSE"),
    ("LT",        "NSE"),
]


class BreakoutPod(BasePod):
    """Opening-range breakout (ORB) intraday strategy."""

    def __init__(self, config: PodConfig, gateway) -> None:
        super().__init__(config, gateway)
        self._highs:   dict[str, float] = {}
        self._lows:    dict[str, float] = {}
        self._volumes: dict[str, deque] = {}
        self._broken:  dict[str, bool]  = {}
        for sym, _ in _WATCHLIST:
            self._highs[sym]   = 0.0
            self._lows[sym]    = float("inf")
            self._volumes[sym] = deque(maxlen=10)
            self._broken[sym]  = False

    def watchlist(self) -> list[tuple[str, str]]:
        return _WATCHLIST

    async def generate_signal(self, quote: Quote) -> Optional[TradeSignal]:
        sym = quote.symbol
        if sym not in self._highs:
            return None

        price = float(quote.ltp)
        self._volumes[sym].append(float(quote.volume or 0))

        # Build opening range from first 6 ticks
        if len(self._volumes[sym]) <= 6:
            self._highs[sym] = max(self._highs[sym], price)
            self._lows[sym]  = min(self._lows[sym], price)
            return None

        if self._broken.get(sym):
            return None

        orb_high = self._highs[sym]
        orb_low  = self._lows[sym]
        if orb_high <= 0 or orb_low == float("inf"):
            return None

        volumes = list(self._volumes[sym])
        avg_vol = sum(volumes[:-1]) / len(volumes[:-1]) if len(volumes) > 1 else 1
        vol_ok  = volumes[-1] > avg_vol * 1.3

        range_size = orb_high - orb_low
        if range_size <= 0:
            return None

        if price > orb_high * 1.001 and vol_ok:
            self._broken[sym] = True
            return TradeSignal(
                symbol=sym,
                exchange=quote.exchange,
                direction=SignalDirection.LONG,
                strength=SignalStrength.STRONG,
                strategy="opening_range_breakout",
                timeframe="1min",
                conviction=0.70,
                entry_price=quote.ltp,
                stop_loss=Decimal(str(round(orb_low, 2))),
                take_profit=quote.ltp + Decimal(str(round(range_size * 2, 2))),
            )

        if price < orb_low * 0.999 and vol_ok:
            self._broken[sym] = True
            return TradeSignal(
                symbol=sym,
                exchange=quote.exchange,
                direction=SignalDirection.SHORT,
                strength=SignalStrength.MODERATE,
                strategy="opening_range_breakout",
                timeframe="1min",
                conviction=0.65,
                entry_price=quote.ltp,
                stop_loss=Decimal(str(round(orb_high, 2))),
                take_profit=quote.ltp - Decimal(str(round(range_size * 2, 2))),
            )

        return None

    def reset_daily(self) -> None:
        for sym in self._highs:
            self._highs[sym]  = 0.0
            self._lows[sym]   = float("inf")
            self._broken[sym] = False


def make_breakout_pod(gateway) -> BreakoutPod:
    config = PodConfig(
        pod_id="breakout_pod_01",
        pod_name="Opening Range Breakout",
        strategy="opening_range_breakout",
        timeframe="1min",
        compatible_regimes=[MarketRegimeTrend.TRENDING],
        capital_budget=Decimal("50000"),
        max_daily_drawdown_pct=2.0,
        max_position_size_pct=20.0,
        stop_loss_pct=1.0,
        max_open_positions=3,
        state=PodState.SANDBOX,
    )
    return BreakoutPod(config, gateway)
