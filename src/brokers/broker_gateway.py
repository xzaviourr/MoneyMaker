"""
BrokerGateway — the ONLY broker import used by the rest of the system.
Routes calls to the active broker implementation, logs every order,
and normalises responses.
"""
from __future__ import annotations

import structlog
from datetime import datetime
from decimal import Decimal
from typing import Callable, Optional

from ..shared.config import toml_cfg
from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    Message,
    MessageType,
    Order,
    OrderBook,
    OrderResult,
    OrderStatus,
    Position,
    Quote,
    AccountBalance,
)
from .base_broker import BaseBroker, QuoteCallback

log = structlog.get_logger(__name__)


class BrokerGateway:
    """
    Singleton gateway.  Instantiate once at startup; share the reference.
    """

    _instance: "BrokerGateway | None" = None

    def __init__(self, broker: BaseBroker) -> None:
        self._broker = broker
        self._bus = MessageBus.get()
        self._order_log: list[dict] = []

    @classmethod
    def get(cls) -> "BrokerGateway":
        if cls._instance is None:
            # Auto-create with paper broker if not yet initialised
            from .paper_broker import PaperBroker
            cls._instance = cls(PaperBroker())
        return cls._instance

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def connect(self) -> None:
        await self._broker.connect()
        log.info("gateway.connected", broker=self._broker.name)

    async def disconnect(self) -> None:
        await self._broker.disconnect()
        log.info("gateway.disconnected", broker=self._broker.name)

    @property
    def is_connected(self) -> bool:
        return self._broker.is_connected

    @property
    def broker_name(self) -> str:
        return self._broker.name

    # ── Account ────────────────────────────────────────────────────────────

    async def get_balance(self) -> AccountBalance:
        return await self._broker.get_balance()

    async def get_positions(self) -> list[Position]:
        return await self._broker.get_positions()

    # ── Market data ────────────────────────────────────────────────────────

    async def get_quote(self, symbol: str, exchange: str) -> Quote:
        return await self._broker.get_quote(symbol, exchange)

    async def get_order_book(self, symbol: str, exchange: str) -> OrderBook:
        return await self._broker.get_order_book(symbol, exchange)

    async def stream_quotes(
        self, symbols: list[tuple[str, str]], callback: QuoteCallback
    ) -> None:
        await self._broker.stream_quotes(symbols, callback)

    def add_symbols(self, callback: QuoteCallback, symbols: list[tuple[str, str]]) -> None:
        """Grow a subscriber's watchlist at runtime — e.g. a stock newly
        mentioned in the news that wasn't being watched before."""
        if hasattr(self._broker, "add_symbols"):
            self._broker.add_symbols(callback, symbols)

    async def stop_stream(self) -> None:
        await self._broker.stop_stream()

    # ── Orders ─────────────────────────────────────────────────────────────

    async def place_order(self, order: Order) -> OrderResult:
        log.info(
            "gateway.place_order",
            symbol=order.symbol,
            side=order.side.value,
            qty=order.quantity,
            type=order.order_type.value,
            pod=order.source_pod,
        )
        result = await self._broker.place_order(order)
        self._order_log.append({
            "order_id": order.id,
            "broker_order_id": result.broker_order_id,
            "symbol": order.symbol,
            "side": order.side.value,
            "qty": order.quantity,
            "status": result.status.value,
            "timestamp": datetime.utcnow().isoformat(),
        })

        msg_type = (
            MessageType.ORDER_FILLED if result.status == OrderStatus.FILLED
            else MessageType.ORDER_PLACED
        )
        await self._bus.publish(
            Message(type=msg_type, source="broker_gateway", payload={
                "order": order.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
            })
        )
        return result

    async def modify_order(self, broker_order_id: str, updates: dict) -> OrderResult:
        log.info("gateway.modify_order", broker_order_id=broker_order_id)
        return await self._broker.modify_order(broker_order_id, updates)

    async def cancel_order(self, broker_order_id: str) -> bool:
        log.info("gateway.cancel_order", broker_order_id=broker_order_id)
        success = await self._broker.cancel_order(broker_order_id)
        if success:
            await self._bus.publish(
                Message(
                    type=MessageType.ORDER_CANCELLED,
                    source="broker_gateway",
                    payload={"broker_order_id": broker_order_id},
                )
            )
        return success

    async def get_order_status(self, broker_order_id: str) -> OrderResult:
        return await self._broker.get_order_status(broker_order_id)

    async def get_historical_data(
        self, symbol: str, exchange: str, interval: str, start: str, end: str
    ) -> list[dict]:
        return await self._broker.get_historical_data(symbol, exchange, interval, start, end)

    async def get_trade_book(self) -> list[dict]:
        return await self._broker.get_trade_book()

    async def purge_position(self, symbol: str, exchange: str) -> Optional[Position]:
        if hasattr(self._broker, "purge_position"):
            return await self._broker.purge_position(symbol, exchange)
        return None

    async def mark_to_market(self) -> None:
        if hasattr(self._broker, "mark_to_market"):
            await self._broker.mark_to_market()

    # ── Factory ────────────────────────────────────────────────────────────

    @classmethod
    def from_config(cls, paper: bool = False) -> "BrokerGateway":
        broker_name = "paper" if paper else toml_cfg.get("broker", {}).get("default", "paper")
        if broker_name == "paper":
            from .paper_broker import PaperBroker
            broker: BaseBroker = PaperBroker()
        elif broker_name == "five_paisa":
            from .five_paisa.broker import FivePaisaBroker
            broker = FivePaisaBroker()
        else:
            raise ValueError(f"Unknown broker: {broker_name}")
        instance = cls(broker)
        # Store as singleton
        cls._instance = instance
        return instance
