"""
MeanReversionPod — Bollinger Band + RSI extremes.
Timeframe: 5–30 min.  Compatible regimes: Choppy, Mean-Reverting.
"""
from __future__ import annotations

from collections import defaultdict, deque
from decimal import Decimal
from typing import Optional

import numpy as np
import structlog

from ...shared.schemas import (
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
from ...shared.config import toml_cfg

log = structlog.get_logger(__name__)

_DEFAULT_WATCHLIST = [
    ("HDFCBANK", "NSE"), ("ICICIBANK", "NSE"), ("SBIN", "NSE"),
    ("WIPRO", "NSE"), ("TECHM", "NSE"), ("LT", "NSE"),
    ("ADANIPORTS", "NSE"), ("BPCL", "NSE"), ("HINDUNILVR", "NSE"),
    ("TITAN", "NSE"),
]


def _load_watchlist() -> list[tuple[str, str]]:
    syms = toml_cfg.get("watchlists", {}).get("mean_rev", [])
    return [(s, "NSE") for s in syms] if syms else _DEFAULT_WATCHLIST


class MeanReversionPod(BasePod):
    """
    Signal logic:
    - Price touches lower Bollinger Band AND RSI < 30 → oversold → BUY
    - Price touches upper Bollinger Band AND RSI > 70 → overbought → SELL
    - Conviction scales with BB distance and RSI extremity
    """

    def __init__(
        self,
        gateway: BrokerGateway,
        bb_period: int = 20,
        bb_std: float = 2.0,
        rsi_period: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
    ) -> None:
        config = PodConfig(
            pod_id="mean_reversion_pod",
            pod_name="MeanReversionPod",
            strategy="bollinger_rsi",
            timeframe="5m",
            compatible_regimes=[MarketRegimeTrend.MEAN_REVERTING, MarketRegimeTrend.CHOPPY],
            state=PodState.SANDBOX,
            params={"bb_period": bb_period, "rsi_period": rsi_period},
        )
        super().__init__(config, gateway)
        self._bb_period   = bb_period
        self._bb_std      = bb_std
        self._rsi_period  = rsi_period
        self._rsi_os      = rsi_oversold
        self._rsi_ob      = rsi_overbought
        self._prices: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=100))

    def watchlist(self) -> list[tuple[str, str]]:
        return _load_watchlist()

    async def generate_signal(self, quote: Quote) -> Optional[TradeSignal]:
        key = quote.symbol
        price = float(quote.ltp)
        self._prices[key].append(price)
        prices = list(self._prices[key])

        if len(prices) < max(self._bb_period, self._rsi_period) + 1:
            return None

        mid  = float(np.mean(prices[-self._bb_period:]))
        std  = float(np.std(prices[-self._bb_period:]))
        upper = mid + self._bb_std * std
        lower = mid - self._bb_std * std

        rsi = self._compute_rsi(prices, self._rsi_period)

        # Oversold: price at lower BB AND RSI < threshold
        if price <= lower and rsi < self._rsi_os:
            bb_dist_pct  = (lower - price) / lower * 100
            rsi_extreme  = (self._rsi_os - rsi) / self._rsi_os
            conviction   = min(0.88, 0.4 + bb_dist_pct * 5 + rsi_extreme * 0.3)
            target       = Decimal(str(mid))
            stop         = Decimal(str(price * 0.985))
            return TradeSignal(
                symbol=quote.symbol,
                exchange=quote.exchange,
                direction=SignalDirection.LONG,
                strength=SignalStrength.STRONG if rsi < 25 else SignalStrength.MODERATE,
                strategy=self.config.strategy,
                conviction=conviction,
                entry_price=quote.ltp,
                stop_loss=stop,
                take_profit=target,
                timeframe=self.config.timeframe,
                regime_compatible=self.config.compatible_regimes,
                rationale=f"Oversold: BB={price:.2f}<{lower:.2f}, RSI={rsi:.1f}",
            )

        # Overbought: price at upper BB AND RSI > threshold
        if price >= upper and rsi > self._rsi_ob:
            bb_dist_pct  = (price - upper) / upper * 100
            rsi_extreme  = (rsi - self._rsi_ob) / (100 - self._rsi_ob)
            conviction   = min(0.88, 0.4 + bb_dist_pct * 5 + rsi_extreme * 0.3)
            target       = Decimal(str(mid))
            stop         = Decimal(str(price * 1.015))
            return TradeSignal(
                symbol=quote.symbol,
                exchange=quote.exchange,
                direction=SignalDirection.SHORT,
                strength=SignalStrength.STRONG if rsi > 75 else SignalStrength.MODERATE,
                strategy=self.config.strategy,
                conviction=conviction,
                entry_price=quote.ltp,
                stop_loss=stop,
                take_profit=target,
                timeframe=self.config.timeframe,
                regime_compatible=self.config.compatible_regimes,
                rationale=f"Overbought: BB={price:.2f}>{upper:.2f}, RSI={rsi:.1f}",
            )
        return None

    @staticmethod
    def _compute_rsi(prices: list[float], period: int) -> float:
        if len(prices) < period + 1:
            return 50.0
        deltas = np.diff(prices[-(period + 1):])
        gains  = deltas[deltas > 0]
        losses = -deltas[deltas < 0]
        avg_gain = float(np.mean(gains)) if len(gains) > 0 else 0.0
        avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)
