"""
BaseStrategy — abstract base for all long-term signal generators.
Each strategy runs asynchronously and feeds StrategySignals into SignalAggregator.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Optional

import structlog

from ...shared.schemas import Exchange, StrategySignal

log = structlog.get_logger(__name__)


class BaseStrategy(ABC):
    """
    Subclass and implement analyse().
    Call self.emit_signal() to submit a signal to the aggregator.
    """

    def __init__(self) -> None:
        self._signal_queue: list[StrategySignal] = []

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    def default_expiry_hours(self) -> int:
        return 48

    @abstractmethod
    async def analyse(self, symbol: str, exchange: Exchange) -> Optional[StrategySignal]:
        """
        Analyse the given symbol and return a StrategySignal or None.
        Must use yfinance or broker data — never hardcode prices.
        """

    async def scan_universe(self, universe: list[tuple[str, str]]) -> list[StrategySignal]:
        """Scan a list of symbols and return all signals found."""
        signals = []
        for symbol, exchange in universe:
            try:
                sig = await self.analyse(symbol, Exchange(exchange))
                if sig and not sig.is_expired:
                    signals.append(sig)
            except Exception as exc:
                log.error(
                    "strategy.scan_error",
                    strategy=self.name,
                    symbol=symbol,
                    error=str(exc),
                )
        return signals

    def _make_expiry(self, hours: Optional[int] = None) -> datetime:
        return datetime.utcnow() + timedelta(hours=hours or self.default_expiry_hours)
