"""
BreakoutPod — Range breakout + volatility filter.
Timeframe: 15–60 min.  Compatible regimes: Trending, Low-Vol.
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
    ("NIFTY", "NSE"), ("RELIANCE", "NSE"), ("TATAMOTORS", "NSE"),
    ("WIPRO", "NSE"), ("SUNPHARMA", "NSE"), ("BAJAJFINSV", "NSE"),
    ("MARUTI", "NSE"), ("TATASTEEL", "NSE"), ("HCLTECH", "NSE"),
    ("POWERGRID", "NSE"),
]


def _load_watchlist() -> list[tuple[str, str]]:
    syms = toml_cfg.get("watchlists", {}).get("breakout", [])
    return [(s, "NSE") for s in syms] if syms else _DEFAULT_WATCHLIST


class BreakoutPod(BasePod):
    """
    Signal logic:
    - Build N-bar high/low range (default 20 bars)
    - On break above range high with volume multiplier → BUY
    - On break below range low with volume multiplier → SELL
    - ATR-based stop loss
    """

    def __init__(
        self,
        gateway: BrokerGateway,
        lookback: int = 20,
        volume_multiplier: float = 1.8,
        atr_stop_mult: float = 2.0,
    ) -> None:
        config = PodConfig(
            pod_id="breakout_pod",
            pod_name="BreakoutPod",
            strategy="range_breakout_vol",
            timeframe="15m",
            compatible_regimes=[MarketRegimeTrend.TRENDING],
            state=PodState.SANDBOX,
            params={"lookback": lookback, "volume_multiplier": volume_multiplier},
        )
        super().__init__(config, gateway)
        self._lookback   = lookback
        self._vol_mult   = volume_multiplier
        self._atr_stop   = atr_stop_mult
        self._prices: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=lookback + 5))
        self._highs:  dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=lookback))
        self._lows:   dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=lookback))
        self._vols:   dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=25))

    def watchlist(self) -> list[tuple[str, str]]:
        return _load_watchlist()

    async def generate_signal(self, quote: Quote) -> Optional[TradeSignal]:
        key = quote.symbol
        price = float(quote.ltp)
        high  = float(quote.high)
        low   = float(quote.low)
        vol   = float(quote.volume)

        self._prices[key].append(price)
        self._highs[key].append(high)
        self._lows[key].append(low)
        self._vols[key].append(vol)

        if len(self._prices[key]) < self._lookback:
            return None

        # Already in a trade for this symbol — check live positions so re-entry
        # is possible after a position closes (unlike _in_trade which never cleared)
        pos_key = f"{quote.symbol}_{quote.exchange.value}"
        if pos_key in self._positions:
            return None

        range_high = max(list(self._highs[key])[:-1])
        range_low  = min(list(self._lows[key])[:-1])
        avg_vol    = float(np.mean(list(self._vols[key])[:-1]))
        vol_ok     = vol >= avg_vol * self._vol_mult

        # ATR for stop computation
        prices_list = list(self._prices[key])
        atr = self._compute_atr(prices_list, period=14)

        # Skip if ATR is too small — expected gain won't cover brokerage + STT fees.
        # Need at least 0.4% price move (e.g. ₹0.70 on a ₹175 stock) to be worthwhile.
        min_atr = price * 0.004
        if atr < min_atr:
            log.debug("breakout.skip_low_volatility", symbol=key,
                      atr=round(atr, 2), min_required=round(min_atr, 2))
            return None

        if price > range_high and vol_ok:
            conviction = min(0.85, 0.5 + (price - range_high) / range_high * 100 * 0.1)
            return TradeSignal(
                symbol=quote.symbol,
                exchange=quote.exchange,
                direction=SignalDirection.LONG,
                strength=SignalStrength.STRONG,
                strategy=self.config.strategy,
                conviction=conviction,
                entry_price=quote.ltp,
                stop_loss=Decimal(str(price - self._atr_stop * atr)),
                take_profit=Decimal(str(price + 3 * self._atr_stop * atr)),
                timeframe=self.config.timeframe,
                regime_compatible=self.config.compatible_regimes,
                rationale=f"Breakout above {range_high:.2f}, vol={vol/avg_vol:.1f}x",
            )
        if price < range_low and vol_ok:
            conviction = min(0.85, 0.5 + (range_low - price) / range_low * 100 * 0.1)
            return TradeSignal(
                symbol=quote.symbol,
                exchange=quote.exchange,
                direction=SignalDirection.SHORT,
                strength=SignalStrength.STRONG,
                strategy=self.config.strategy,
                conviction=conviction,
                entry_price=quote.ltp,
                stop_loss=Decimal(str(price + self._atr_stop * atr)),
                take_profit=Decimal(str(price - 3 * self._atr_stop * atr)),
                timeframe=self.config.timeframe,
                regime_compatible=self.config.compatible_regimes,
                rationale=f"Breakdown below {range_low:.2f}, vol={vol/avg_vol:.1f}x",
            )
        return None

    @staticmethod
    def _compute_atr(prices: list[float], period: int = 14) -> float:
        if len(prices) < 2:
            return prices[-1] * 0.01 if prices else 1.0
        trs = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
        return float(np.mean(trs[-period:])) if trs else 1.0
