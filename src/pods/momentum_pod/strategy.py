"""
MomentumPod — EMA crossover + volume confirmation.
Timeframe: 5–15 min.  Compatible regimes: Trending, Risk-On.
"""
from __future__ import annotations

from collections import defaultdict, deque
from decimal import Decimal
from typing import Optional

import numpy as np
import structlog

from ...shared.schemas import (
    Exchange,
    MarketRegimeTrend,
    PodConfig,
    PodState,
    Quote,
    SignalDirection,
    SignalStrength,
    TradeSignal,
)
from ..base_pod import BasePod
from ...brokers.broker_gateway import BrokerGateway

log = structlog.get_logger(__name__)

_DEFAULT_WATCHLIST = [
    ("RELIANCE", "NSE"), ("TCS", "NSE"), ("INFY", "NSE"),
    ("HDFCBANK", "NSE"), ("ICICIBANK", "NSE"), ("SBIN", "NSE"),
    ("KOTAKBANK", "NSE"), ("BHARTIARTL", "NSE"), ("ITC", "NSE"),
    ("AXISBANK", "NSE"),
]


class MomentumPod(BasePod):
    """
    Signal logic:
    - EMA9 crosses above EMA21 + volume > 1.5x 20-period avg → BUY signal
    - EMA9 crosses below EMA21 + volume confirmation → SELL signal
    - Conviction scales with EMA spread and volume multiple
    """

    def __init__(
        self,
        gateway: BrokerGateway,
        fast_ema: int = 9,
        slow_ema: int = 21,
        volume_multiplier: float = 1.5,
        watchlist: Optional[list[tuple[str, str]]] = None,
    ) -> None:
        config = PodConfig(
            pod_id="momentum_pod",
            pod_name="MomentumPod",
            strategy="ema_crossover_volume",
            timeframe="5m",
            compatible_regimes=[MarketRegimeTrend.TRENDING],
            state=PodState.SANDBOX,
            params={"fast_ema": fast_ema, "slow_ema": slow_ema,
                    "volume_multiplier": volume_multiplier},
        )
        super().__init__(config, gateway)
        self._fast_period  = fast_ema
        self._slow_period  = slow_ema
        self._vol_mult     = volume_multiplier
        self._watchlist    = watchlist or _DEFAULT_WATCHLIST

        # Per-symbol price and volume queues
        self._prices: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=max(slow_ema * 3, 60))
        )
        self._volumes: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=25)
        )
        self._prev_cross: dict[str, Optional[str]] = {}  # "bull" | "bear" | None

    def watchlist(self) -> list[tuple[str, str]]:
        return self._watchlist

    async def generate_signal(self, quote: Quote) -> Optional[TradeSignal]:
        key = quote.symbol
        price = float(quote.ltp)
        volume = float(quote.volume)

        self._prices[key].append(price)
        self._volumes[key].append(volume)

        prices = list(self._prices[key])
        vols   = list(self._volumes[key])

        if len(prices) < self._slow_period + 1:
            return None

        fast_ema = self._ema(prices, self._fast_period)
        slow_ema = self._ema(prices, self._slow_period)
        prev_fast = self._ema(prices[:-1], self._fast_period)
        prev_slow = self._ema(prices[:-1], self._slow_period)

        avg_vol = float(np.mean(vols[:-1])) if len(vols) > 1 else volume
        vol_ok  = volume >= avg_vol * self._vol_mult

        # EMA crossover detection
        cross = None
        if prev_fast <= prev_slow and fast_ema > slow_ema:
            cross = "bull"
        elif prev_fast >= prev_slow and fast_ema < slow_ema:
            cross = "bear"

        if not cross or not vol_ok:
            return None

        # Avoid duplicate signals
        if self._prev_cross.get(key) == cross:
            return None
        self._prev_cross[key] = cross

        spread_pct = abs(fast_ema - slow_ema) / slow_ema * 100
        conviction = min(0.9, 0.4 + spread_pct * 10 + (volume / avg_vol - 1) * 0.1)

        direction  = SignalDirection.LONG if cross == "bull" else SignalDirection.SHORT
        strength   = SignalStrength.STRONG if conviction > 0.7 else SignalStrength.MODERATE
        stop_mult  = Decimal("0.985") if direction == SignalDirection.LONG else Decimal("1.015")

        return TradeSignal(
            symbol=quote.symbol,
            exchange=quote.exchange,
            direction=direction,
            strength=strength,
            strategy=self.config.strategy,
            conviction=conviction,
            entry_price=quote.ltp,
            stop_loss=quote.ltp * stop_mult,
            timeframe=self.config.timeframe,
            regime_compatible=self.config.compatible_regimes,
            rationale=(
                f"EMA{self._fast_period}({'>'if cross=='bull' else '<'})"
                f"EMA{self._slow_period}, vol={volume/avg_vol:.1f}x avg"
            ),
        )

    @staticmethod
    def _ema(prices: list[float], period: int) -> float:
        if len(prices) < period:
            return float(np.mean(prices))
        k = 2 / (period + 1)
        ema = float(np.mean(prices[:period]))
        for p in prices[period:]:
            ema = p * k + ema * (1 - k)
        return ema
