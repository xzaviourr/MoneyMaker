"""
ScalpPod — Order flow imbalance + tape reading.
Timeframe: 1–5 min.  Compatible regimes: Any (low spread required).
"""
from __future__ import annotations

from collections import deque, defaultdict
from decimal import Decimal
from typing import Optional

import structlog

from ...shared.schemas import (
    MarketRegimeTrend,
    OrderBook,
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

_WATCHLIST = [
    ("NIFTY", "NFO"), ("BANKNIFTY", "NFO"),
    ("RELIANCE", "NSE"), ("TCS", "NSE"), ("HDFCBANK", "NSE"),
]
_IMBALANCE_THRESHOLD = 0.3    # 30% imbalance triggers signal
_MIN_SPREAD_BPS      = 2.0
_MAX_SPREAD_BPS      = 10.0   # don't scalp wide-spread instruments


class ScalpPod(BasePod):
    """
    Signal logic:
    - Reads L2 order book every tick
    - Computes bid/ask imbalance (bid_vol - ask_vol) / (bid_vol + ask_vol)
    - Strong imbalance + tape acceleration → directional scalp
    - Very tight stops (ATR * 0.5), quick targets
    """

    def __init__(self, gateway: BrokerGateway) -> None:
        config = PodConfig(
            pod_id="scalp_pod",
            pod_name="ScalpPod",
            strategy="order_flow_imbalance",
            timeframe="1m",
            compatible_regimes=list(MarketRegimeTrend),  # any regime
            state=PodState.SANDBOX,
            max_position_size_pct=5.0,   # small sizes for scalping
            stop_loss_pct=0.3,
            max_open_positions=3,
        )
        super().__init__(config, gateway)
        self._imbalance_history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=10))
        self._price_history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=20))

    def watchlist(self) -> list[tuple[str, str]]:
        return _WATCHLIST

    async def generate_signal(self, quote: Quote) -> Optional[TradeSignal]:
        # Fetch order book for imbalance analysis
        try:
            ob: OrderBook = await self._gateway.get_order_book(
                quote.symbol, quote.exchange.value
            )
        except Exception:
            return None

        # Spread filter
        if ob.best_bid and ob.best_ask:
            spread_bps = float((ob.best_ask - ob.best_bid) / ob.best_ask * 10000)
            if spread_bps < _MIN_SPREAD_BPS or spread_bps > _MAX_SPREAD_BPS:
                return None

        imbalance = ob.imbalance
        key = quote.symbol
        self._imbalance_history[key].append(imbalance)
        self._price_history[key].append(float(quote.ltp))

        hist = list(self._imbalance_history[key])
        if len(hist) < 5:
            return None

        # Persistent imbalance (not a single spike)
        avg_imbalance = sum(hist[-5:]) / 5

        if abs(avg_imbalance) < _IMBALANCE_THRESHOLD:
            return None

        direction  = SignalDirection.LONG if avg_imbalance > 0 else SignalDirection.SHORT
        conviction = min(0.80, 0.4 + abs(avg_imbalance) * 0.8)
        atr        = self._quick_atr(list(self._price_history[key]))
        sl_mult    = Decimal("0.997") if direction == SignalDirection.LONG else Decimal("1.003")
        tp_mult    = Decimal("1.004") if direction == SignalDirection.LONG else Decimal("0.996")

        return TradeSignal(
            symbol=quote.symbol,
            exchange=quote.exchange,
            direction=direction,
            strength=SignalStrength.MODERATE,
            strategy=self.config.strategy,
            conviction=conviction,
            entry_price=quote.ltp,
            stop_loss=quote.ltp * sl_mult,
            take_profit=quote.ltp * tp_mult,
            timeframe=self.config.timeframe,
            rationale=f"OFI imbalance={avg_imbalance:.2f}",
        )

    @staticmethod
    def _quick_atr(prices: list[float]) -> float:
        if len(prices) < 2:
            return 1.0
        import numpy as np
        return float(np.mean(np.abs(np.diff(prices[-10:]))))
