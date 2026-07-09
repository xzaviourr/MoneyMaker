"""
PositionMonitor — streams live P&L, enforces stop-loss and trailing stops.
Auto-exits on hard stop breach (no approval required).
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Optional

import structlog

from ..brokers.broker_gateway import BrokerGateway
from ..shared.config import toml_cfg
from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    GuardianAlert,
    GuardianResponseMode,
    Message,
    MessageType,
    Order,
    OrderSide,
    OrderType,
    Position,
    Quote,
)

log = structlog.get_logger(__name__)


class PositionMonitor:
    """
    Streams live P&L for all open positions across all pillars.
    Enforces hard stop-loss and trailing stop rules.
    """

    def __init__(self, gateway: BrokerGateway) -> None:
        cfg = toml_cfg.get("guardian", {})
        self._default_sl_pct      = float(cfg.get("stop_loss_default_pct", 2.0))
        self._trailing_sl_pct     = float(cfg.get("trailing_stop_default_pct", 1.5))
        self._gateway             = gateway
        self._bus                 = MessageBus.get()
        self._positions: dict[str, Position] = {}
        self._high_water: dict[str, Decimal] = {}  # for trailing stop
        self._lock = asyncio.Lock()

        # Subscribe to guardian quotes
        self._bus.subscribe(MessageType.QUOTE_UPDATE, self._on_quote)

    # ── Position management ────────────────────────────────────────────────

    async def register_position(self, position: Position) -> None:
        async with self._lock:
            key = f"{position.symbol}_{position.exchange.value}"
            self._positions[key] = position
            self._high_water[key] = position.current_price

    async def unregister_position(self, symbol: str, exchange: str) -> None:
        async with self._lock:
            key = f"{symbol}_{exchange}"
            self._positions.pop(key, None)
            self._high_water.pop(key, None)

    async def sync_positions(self) -> None:
        """Pull current positions from broker and sync internal state."""
        positions = await self._gateway.get_positions()
        async with self._lock:
            self._positions = {
                f"{p.symbol}_{p.exchange.value}": p for p in positions
            }

    # ── Quote handler ──────────────────────────────────────────────────────

    async def _on_quote(self, message: Message) -> None:
        quote = Quote(**message.payload)
        key   = f"{quote.symbol}_{quote.exchange.value}"

        async with self._lock:
            pos = self._positions.get(key)
            if not pos:
                return
            hw  = self._high_water.get(key, pos.average_price)

        # Update high water mark for trailing stop
        if quote.ltp > hw and pos.side == OrderSide.BUY:
            async with self._lock:
                self._high_water[key] = quote.ltp

        reason = self._check_exit(pos, quote)
        if reason:
            await self._exit_position(pos, quote, reason=reason)

    async def check_exits(self) -> None:
        """Polling fallback — called periodically (see PortfolioGuardian._sync_loop)
        since nothing in the system actually publishes QUOTE_UPDATE on the bus,
        so _on_quote above never fires on its own. This is what makes stop-loss/
        target/time-stop actually run for every open position, pod or desk."""
        async with self._lock:
            positions = list(self._positions.values())

        for pos in positions:
            try:
                quote = await self._gateway.get_quote(pos.symbol, pos.exchange.value)
            except Exception:
                continue
            key = f"{pos.symbol}_{pos.exchange.value}"
            hw  = self._high_water.get(key, pos.average_price)
            if quote.ltp > hw and pos.side == OrderSide.BUY:
                async with self._lock:
                    self._high_water[key] = quote.ltp
            reason = self._check_exit(pos, quote)
            if reason:
                await self._exit_position(pos, quote, reason=reason)

    def _check_exit(self, pos: Position, quote: Quote) -> Optional[str]:
        """Returns a human-readable exit reason, or None if the position should stay open."""
        if pos.take_profit:
            hit = (quote.ltp >= pos.take_profit if pos.side == OrderSide.BUY
                   else quote.ltp <= pos.take_profit)
            if hit:
                return f"Target hit: ₹{float(quote.ltp):.2f} vs target ₹{float(pos.take_profit):.2f}"

        if pos.max_hold_until and datetime.utcnow() >= pos.max_hold_until:
            return f"Max holding time reached ({pos.max_hold_until.date()})"

        key = f"{pos.symbol}_{pos.exchange.value}"
        effective_stop = self._compute_stop(pos, self._high_water.get(key, pos.average_price))
        if (pos.side == OrderSide.BUY and quote.ltp <= effective_stop) or \
           (pos.side == OrderSide.SELL and quote.ltp >= effective_stop):
            log.warning("position_monitor.stop_breach", symbol=pos.symbol,
                        price=str(quote.ltp), stop=str(effective_stop))
            return f"Stop-loss triggered: {quote.ltp} vs stop {effective_stop}"

        return None

    def _compute_stop(self, pos: Position, high_water: Decimal) -> Decimal:
        """Returns the effective stop price (hard SL or trailing, whichever is tighter).

        Trailing stop only activates once the position is at least 1% profitable —
        prevents normal intraday noise (0.5-0.8% swings) from triggering exits before
        the trade has had a chance to develop.
        """
        hard_sl = pos.stop_loss or (
            pos.average_price * Decimal(str(1 - self._default_sl_pct / 100))
        )
        if pos.trailing_stop_pct:
            activation_price = pos.average_price * Decimal("1.01") if pos.side == OrderSide.BUY \
                else pos.average_price * Decimal("0.99")
            trail_activated = (high_water >= activation_price) if pos.side == OrderSide.BUY \
                else (high_water <= activation_price)
            if trail_activated:
                trailing_sl = high_water * Decimal(str(1 - pos.trailing_stop_pct / 100))
                return max(hard_sl, trailing_sl) if pos.side == OrderSide.BUY else min(hard_sl, trailing_sl)
        return hard_sl

    async def _exit_position(self, pos: Position, quote: Quote, reason: str) -> None:
        order = Order(
            symbol=pos.symbol,
            exchange=pos.exchange,
            side=OrderSide.SELL if pos.side == OrderSide.BUY else OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=pos.quantity,
            source_pod=pos.source_pod,
        )
        try:
            result = await self._gateway.place_order(order)
            key = f"{pos.symbol}_{pos.exchange.value}"
            async with self._lock:
                self._positions.pop(key, None)
            pnl = (result.average_fill_price - pos.average_price) * pos.quantity \
                if pos.side == OrderSide.BUY and result.average_fill_price else None
            log.info(
                "position_monitor.exited",
                symbol=pos.symbol,
                reason=reason,
                fill_price=str(result.average_fill_price),
                pnl=str(pnl) if pnl is not None else None,
            )
            from ..audit.explainability_ledger import ExplainabilityLedger
            await ExplainabilityLedger.get().record(
                agent_id="position_monitor",
                decision="sell" if pos.side == OrderSide.BUY else "buy",
                reasoning=reason,
                symbol=pos.symbol,
                inputs={"entry_price": str(pos.average_price), "held_since": str(pos.opened_at)},
                outputs={"quantity": pos.quantity, "fill_price": str(result.average_fill_price),
                         "pnl": str(pnl) if pnl is not None else None},
            )
        except Exception as exc:
            log.error("position_monitor.exit_failed", symbol=pos.symbol, error=str(exc))

        alert = GuardianAlert(
            mode=GuardianResponseMode.ALERT,
            symbol=pos.symbol,
            position_id=pos.id,
            severity="warning",
            reason=reason,
            auto_executed=True,
        )
        await self._bus.publish(
            Message(type=MessageType.GUARDIAN_ALERT, source="position_monitor",
                    payload=alert.model_dump(mode="json"))
        )

    def get_all_positions(self) -> list[Position]:
        return list(self._positions.values())

    def get_portfolio_pnl(self) -> Decimal:
        return sum(p.unrealized_pnl for p in self._positions.values())
