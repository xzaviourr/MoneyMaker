"""
OutcomeAttributionTimer — waits for a position to close and then triggers attribution.

Some trades are held for weeks. This component polls open positions, detects when they close,
and fires TradeAttributionEngine.attribute() with the completed Trade record.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from ..brokers.broker_gateway import BrokerGateway
from ..shared import feature_toggles
from ..shared.message_bus import MessageBus
from ..shared.schemas import Message, MessageType, Trade
from .trade_attribution_engine import TradeAttributionEngine

log = structlog.get_logger(__name__)

_SEEN_PATH = Path("data/attributed_trades.json")


class OutcomeAttributionTimer:
    """Polls closed trades every 5 minutes and triggers attribution."""

    def __init__(self, attribution_engine: TradeAttributionEngine) -> None:
        self._engine   = attribution_engine
        self._seen:    set[str] = self._load_seen()   # trade_ids already attributed
        self._bus      = MessageBus.get()
        self._running  = False

    @staticmethod
    def _load_seen() -> set[str]:
        # Without this, every restart forgets everything it already processed
        # and re-attributes the entire trade history from trade #1 — each one
        # making a real network call, so the backlog never finishes and the
        # Feedback page never gets populated.
        if not _SEEN_PATH.exists():
            return set()
        try:
            return set(json.loads(_SEEN_PATH.read_text()))
        except Exception:
            return set()

    def _save_seen(self) -> None:
        try:
            _SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
            _SEEN_PATH.write_text(json.dumps(list(self._seen)))
        except Exception:
            log.exception("attribution_timer.save_seen_failed")

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._poll_loop())
        self._bus.subscribe(MessageType.ORDER_FILLED, self._on_fill)
        log.info("outcome_attribution_timer.started", already_attributed=len(self._seen))

    async def stop(self) -> None:
        self._running = False

    async def _poll_loop(self) -> None:
        while self._running:
            if feature_toggles.is_enabled("feedback"):
                try:
                    await self._check_closed_trades()
                except Exception:
                    log.exception("attribution_timer.poll_error")
            await asyncio.sleep(300)  # every 5 minutes

    async def _check_closed_trades(self) -> None:
        broker = BrokerGateway.get()
        if not broker.is_connected:
            return

        try:
            # Fetch today's trade book
            trades = await broker.get_trade_book()
        except Exception:
            return

        new_count = 0
        for trade in trades:
            tid = str(trade.get("trade_id", ""))
            # Only closing (sell) fills carry a real entry→exit move and a
            # realized P&L — a buy fill has nothing to attribute yet.
            if tid and tid not in self._seen and trade.get("side") == "sell":
                try:
                    t_obj = self._make_trade(trade)
                    if t_obj:
                        await self._engine.attribute(t_obj)
                        self._seen.add(tid)
                        new_count += 1
                except Exception:
                    log.exception("attribution_timer.attribute_error", trade_id=tid)
            elif tid:
                self._seen.add(tid)  # buy fills: nothing to do, just stop revisiting

        if new_count:
            self._save_seen()
            log.info("attribution_timer.batch_done", attributed=new_count)

    @staticmethod
    def _make_trade(data: dict[str, Any]) -> Any:
        """Convert a closing (sell) trade_book entry to a Trade schema."""
        try:
            from decimal import Decimal
            from ..shared.schemas import Exchange, OrderSide, SignalDirection, Trade
            entry_price = data.get("entry_price")
            exit_price  = data.get("price")
            # The paper broker never opens a naked short (a sell with no
            # existing position is rejected), so every closing sell trade is
            # always the exit of a long.
            return Trade(
                trade_id=str(data.get("trade_id", "")),
                symbol=str(data.get("symbol", "")),
                exchange=Exchange(data.get("exchange", "NSE")),
                side=OrderSide.SELL,
                price=Decimal(str(exit_price if exit_price is not None else 0)),
                direction=SignalDirection.LONG,
                quantity=int(data.get("quantity", 0)),
                entry_price=float(entry_price) if entry_price is not None else float(exit_price or 0),
                exit_price=float(exit_price) if exit_price is not None else None,
                realized_pnl=float(data.get("pnl", 0)),
                slippage_cost=float(data.get("slippage", 0)),
                entry_time=None,
                exit_time=None,
                source_pod=data.get("source_pod"),
                source_desk=data.get("source_desk"),
                strategy=data.get("strategy"),
            )
        except Exception:
            log.exception("attribution_timer.make_trade_failed", trade_id=data.get("trade_id"))
            return None

    async def _on_fill(self, msg: Message) -> None:
        """Quick path: attribute immediately on fill (for intraday scalp trades)."""
        pass  # handled by poll loop for robustness
