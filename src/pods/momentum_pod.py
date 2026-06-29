"""
MomentumPod — intraday EMA crossover + volume surge strategy.

Signal: fast EMA (9) crosses above slow EMA (21) with volume 1.5× average.
Compatible with TRENDING regimes.
"""
from __future__ import annotations

from collections import deque
from decimal import Decimal
from typing import Optional

import structlog

from ..shared.schemas import (
    Exchange,
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
    ("RELIANCE", "NSE"),
    ("TCS",      "NSE"),
    ("HDFCBANK", "NSE"),
    ("INFY",     "NSE"),
    ("ICICIBANK","NSE"),
]


def _ema(prices: list[float], period: int) -> float:
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    k = 2 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)
    return ema


class MomentumPod(BasePod):
    """EMA-9/21 crossover with volume filter."""

    def __init__(self, config: PodConfig, gateway) -> None:
        super().__init__(config, gateway)
        self._prices:     dict[str, deque] = {}
        self._volumes:    dict[str, deque] = {}
        self._prev_cross: dict[str, str]   = {}   # "above" | "below" | ""
        for sym, _ in _WATCHLIST:
            self._prices[sym]  = deque(maxlen=50)
            self._volumes[sym] = deque(maxlen=20)

    def watchlist(self) -> list[tuple[str, str]]:
        return _WATCHLIST

    async def generate_signal(self, quote: Quote) -> Optional[TradeSignal]:
        sym = quote.symbol
        if sym not in self._prices:
            return None

        self._prices[sym].append(float(quote.ltp))
        self._volumes[sym].append(float(quote.volume or 0))

        prices = list(self._prices[sym])
        if len(prices) < 22:
            return None

        fast = _ema(prices, 9)
        slow = _ema(prices, 21)

        volumes = list(self._volumes[sym])
        avg_vol  = sum(volumes[:-1]) / len(volumes[:-1]) if len(volumes) > 1 else 1
        vol_surge = (volumes[-1] > avg_vol * 1.5) if avg_vol > 0 else False

        prev = self._prev_cross.get(sym, "")
        curr = "above" if fast > slow else "below"
        self._prev_cross[sym] = curr

        # Fresh bullish crossover with volume confirmation
        if curr == "above" and prev == "below" and vol_surge:
            return TradeSignal(
                symbol=sym,
                exchange=quote.exchange,
                direction=SignalDirection.LONG,
                strength=SignalStrength.MODERATE,
                strategy="momentum_ema_crossover",
                timeframe="1min",
                conviction=0.65,
                entry_price=quote.ltp,
                stop_loss=quote.ltp * Decimal("0.985"),
                take_profit=quote.ltp * Decimal("1.02"),
            )

        # Fresh bearish crossover with volume — neutral (no short in simple demo)
        if curr == "below" and prev == "above" and vol_surge:
            return TradeSignal(
                symbol=sym,
                exchange=quote.exchange,
                direction=SignalDirection.NEUTRAL,
                strength=SignalStrength.MODERATE,
                strategy="momentum_ema_crossover",
                timeframe="1min",
                conviction=0.60,
                entry_price=quote.ltp,
            )

        return None


def make_momentum_pod(gateway) -> MomentumPod:
    config = PodConfig(
        pod_id="momentum_pod_01",
        pod_name="Momentum EMA Crossover",
        strategy="momentum_ema_crossover",
        timeframe="1min",
        compatible_regimes=[MarketRegimeTrend.TRENDING],
        capital_budget=Decimal("50000"),
        max_daily_drawdown_pct=2.0,
        max_position_size_pct=20.0,
        stop_loss_pct=1.5,
        max_open_positions=3,
        state=PodState.SANDBOX,
    )
    return MomentumPod(config, gateway)
