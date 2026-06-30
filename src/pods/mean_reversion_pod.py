"""
MeanReversionPod — intraday Bollinger Band mean-reversion strategy.

Signal: price touches lower/upper band (2σ) + RSI oversold/overbought.
Compatible with MEAN_REVERTING and CHOPPY regimes.
"""
from __future__ import annotations

import math
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
    ("AXISBANK",   "NSE"),
    ("SBIN",       "NSE"),
    ("TATAMOTORS", "NSE"),
    ("MARUTI",     "NSE"),
    ("ITC",        "NSE"),
]


def _bollinger(prices: list[float], period: int = 20, k: float = 2.0) -> tuple[float, float, float]:
    if len(prices) < period:
        p = prices[-1]
        return p, p, p
    window = prices[-period:]
    mean = sum(window) / period
    variance = sum((x - mean) ** 2 for x in window) / period
    std = math.sqrt(variance)
    return mean, mean + k * std, mean - k * std


def _rsi(prices: list[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains  = [d for d in deltas[-period:] if d > 0]
    losses = [-d for d in deltas[-period:] if d < 0]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


class MeanReversionPod(BasePod):
    """Bollinger Band 2σ mean reversion with RSI confirmation."""

    def __init__(self, config: PodConfig, gateway) -> None:
        super().__init__(config, gateway)
        self._prices: dict[str, deque] = {}
        for sym, _ in _WATCHLIST:
            self._prices[sym] = deque(maxlen=60)

    def watchlist(self) -> list[tuple[str, str]]:
        return _WATCHLIST

    async def generate_signal(self, quote: Quote) -> Optional[TradeSignal]:
        sym = quote.symbol
        if sym not in self._prices:
            return None

        self._prices[sym].append(float(quote.ltp))
        prices = list(self._prices[sym])

        if len(prices) < 21:
            return None

        mid, upper, lower = _bollinger(prices)
        rsi   = _rsi(prices)
        price = prices[-1]

        key = f"{sym}_{quote.exchange.value}"

        # Long: at/below lower band + RSI oversold
        if price <= lower * 1.001 and rsi < 35 and key not in self._positions:
            return TradeSignal(
                symbol=sym,
                exchange=quote.exchange,
                direction=SignalDirection.LONG,
                strength=SignalStrength.MODERATE,
                strategy="bollinger_mean_reversion",
                timeframe="5min",
                conviction=0.60,
                entry_price=quote.ltp,
                stop_loss=quote.ltp * Decimal("0.98"),
                take_profit=Decimal(str(round(mid, 2))),
            )

        # Short: at/above upper band + RSI overbought
        if price >= upper * 0.999 and rsi > 65 and key not in self._positions:
            return TradeSignal(
                symbol=sym,
                exchange=quote.exchange,
                direction=SignalDirection.SHORT,
                strength=SignalStrength.MODERATE,
                strategy="bollinger_mean_reversion",
                timeframe="5min",
                conviction=0.58,
                entry_price=quote.ltp,
                stop_loss=quote.ltp * Decimal("1.02"),
                take_profit=Decimal(str(round(mid, 2))),
            )

        return None


def make_mean_reversion_pod(gateway) -> MeanReversionPod:
    config = PodConfig(
        pod_id="mean_reversion_pod_01",
        pod_name="Bollinger Mean Reversion",
        strategy="bollinger_mean_reversion",
        timeframe="5min",
        compatible_regimes=[
            MarketRegimeTrend.MEAN_REVERTING,
            MarketRegimeTrend.CHOPPY,
        ],
        capital_budget=Decimal("50000"),
        max_daily_drawdown_pct=1.5,
        max_position_size_pct=15.0,
        stop_loss_pct=2.0,
        max_open_positions=3,
        state=PodState.SANDBOX,
    )
    return MeanReversionPod(config, gateway)
