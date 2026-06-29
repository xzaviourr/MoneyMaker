"""
Abstract broker interface.  Every concrete broker (FivePaisa, Zerodha, Paper)
must implement this.  The rest of the system only imports BaseBroker.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Callable

from ..shared.schemas import (
    AccountBalance,
    Order,
    OrderBook,
    OrderResult,
    Position,
    Quote,
)

QuoteCallback = Callable[[Quote], None]


class BaseBroker(ABC):
    """Provider-agnostic broker interface."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    # ── Authentication ─────────────────────────────────────────────────────

    @abstractmethod
    async def connect(self) -> None:
        """Authenticate and establish session."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Cleanly close session."""

    @property
    @abstractmethod
    def is_connected(self) -> bool: ...

    # ── Account ────────────────────────────────────────────────────────────

    @abstractmethod
    async def get_balance(self) -> AccountBalance:
        """Return current account balance."""

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """Return all open positions."""

    # ── Market data ────────────────────────────────────────────────────────

    @abstractmethod
    async def get_quote(self, symbol: str, exchange: str) -> Quote:
        """Return latest quote for a symbol."""

    @abstractmethod
    async def get_order_book(self, symbol: str, exchange: str) -> OrderBook:
        """Return L2 order book."""

    @abstractmethod
    async def stream_quotes(
        self, symbols: list[tuple[str, str]], callback: QuoteCallback
    ) -> None:
        """Subscribe to live quote stream. symbols = [(ticker, exchange), ...]"""

    @abstractmethod
    async def stop_stream(self) -> None:
        """Unsubscribe and stop the quote stream."""

    # ── Orders ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def place_order(self, order: Order) -> OrderResult:
        """Place an order and return result."""

    @abstractmethod
    async def modify_order(self, broker_order_id: str, updates: dict) -> OrderResult:
        """Modify an open order."""

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel an open order. Returns True on success."""

    @abstractmethod
    async def get_order_status(self, broker_order_id: str) -> OrderResult:
        """Fetch current status of an order."""

    @abstractmethod
    async def get_order_book_orders(self) -> list[Order]:
        """Fetch today's order book (all orders)."""

    # ── Historical data ────────────────────────────────────────────────────

    @abstractmethod
    async def get_historical_data(
        self,
        symbol: str,
        exchange: str,
        interval: str,
        start: str,
        end: str,
    ) -> list[dict]:
        """
        Fetch OHLCV candles.
        interval: '1m' | '5m' | '15m' | '1h' | '1d'
        start/end: ISO date strings
        """

    async def get_trade_book(self) -> list[dict]:
        """Fetch today's executed trades. Default implementation returns empty list."""
        return []
