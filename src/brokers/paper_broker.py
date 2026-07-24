"""
PaperBroker — simulated fills for Sandbox / Shadow / Probation modes.
Configurable slippage model.  Thread-safe via asyncio.Lock.
"""
from __future__ import annotations

import asyncio
import json
import random
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

import structlog

from ..shared.schemas import (
    AccountBalance,
    Exchange,
    Order,
    OrderBook,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Quote,
)
from ..shared.config import toml_cfg
from ..shared.data_paths import DATA_DIR
from ..shared.trade_cost_estimator import (
    current_financial_year_label,
    estimate_capital_gains_tax,
    estimate_trade_cost,
)
from .base_broker import BaseBroker, QuoteCallback

log = structlog.get_logger(__name__)

_STATE_PATH = DATA_DIR / "paper_broker_state.json"

# Positions are tracked globally per symbol, not per pod/desk. Only these
# system-level managers are allowed to exit a position they didn't open
# themselves (portfolio_manager rebalances/exits any holding; position_monitor
# instead re-tags its own exits with the *original* position's source, so it
# never needs to appear here). Anyone else must own the position it's exiting —
# otherwise one pod's/desk's independent signal can silently close a position
# opened by a completely different one, just because they share a symbol.
_CROSS_SOURCE_EXIT_ALLOWED = {"portfolio_manager"}


class PaperBroker(BaseBroker):
    """
    Simulates broker fills with:
    - configurable slippage (bps)
    - flat commission per order
    - in-memory position tracking
    - async quote streaming (mock)
    """

    name = "paper"

    def __init__(self) -> None:
        cfg = toml_cfg.get("broker", {}).get("paper", {})
        self._slippage_bps = Decimal(str(cfg.get("slippage_bps", 5.0)))
        self._balance = Decimal(str(
            toml_cfg.get("capital", {}).get("total_capital", 1_000_000)
        ))
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, Order] = {}
        self._trade_book: list[dict] = []
        # ₹1.25L LTCG exemption is per-financial-year, not per-trade — track
        # cumulative LTCG booked so far this FY so it's applied progressively
        # instead of every long-hold winner getting the exemption from scratch.
        self._ltcg_realized_this_fy = Decimal("0")
        self._fy_label = current_financial_year_label(datetime.utcnow())
        self._lock = asyncio.Lock()
        self._connected = False
        self._stream_task: asyncio.Task | None = None  # type: ignore[type-arg]
        # Every pod shares this one broker instance — each needs its OWN slot,
        # not a single overwritable callback, or only the last pod to subscribe
        # would ever receive a tick.
        self._subscriptions: list[tuple[list[tuple[str, str]], QuoteCallback]] = []
        self._prices: dict[str, Decimal] = {}
        self._load_state()  # resume balance/positions/trades from before a restart, if any

    # ── Persistence ────────────────────────────────────────────────────────
    # Without this, every restart silently wipes the account back to the
    # config default — any open positions, P&L, and trade history are lost.

    def _load_state(self) -> None:
        if not _STATE_PATH.exists():
            return
        try:
            data = json.loads(_STATE_PATH.read_text())
            self._balance = Decimal(data["balance"])
            self._positions = {k: Position(**v) for k, v in data.get("positions", {}).items()}
            self._trade_book = data.get("trade_book", [])
            # Only carry the LTCG-exemption counter forward if we're still in
            # the same Indian financial year (Apr-Mar) it was recorded in —
            # otherwise a restart in a new FY would wrongly keep last year's
            # exemption usage and under-apply this year's ₹1.25L allowance.
            if data.get("fy_label") == self._fy_label:
                self._ltcg_realized_this_fy = Decimal(data.get("ltcg_realized_this_fy", "0"))
            log.info("paper_broker.state_restored", balance=str(self._balance),
                     positions=len(self._positions), trades=len(self._trade_book))
        except Exception as exc:
            log.error("paper_broker.state_restore_failed", error=str(exc))

    def _save_state(self) -> None:
        try:
            _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "balance":    str(self._balance),
                "positions":  {k: json.loads(v.model_dump_json()) for k, v in self._positions.items()},
                "trade_book": self._trade_book,
                "fy_label":   self._fy_label,
                "ltcg_realized_this_fy": str(self._ltcg_realized_this_fy),
            }
            tmp = _STATE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            import os; os.replace(tmp, _STATE_PATH)
        except Exception as exc:
            log.error("paper_broker.state_save_failed", error=str(exc))

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def connect(self) -> None:
        self._connected = True
        log.info("paper_broker.connected")

    async def disconnect(self) -> None:
        await self.stop_stream()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Account ────────────────────────────────────────────────────────────

    async def get_balance(self) -> AccountBalance:
        async with self._lock:
            unrealized = sum(p.unrealized_pnl for p in self._positions.values())
            return AccountBalance(
                total=self._balance + unrealized,
                available=self._balance,
                unrealized_pnl=unrealized,
            )

    async def get_positions(self) -> list[Position]:
        async with self._lock:
            return list(self._positions.values())

    # ── Market data ────────────────────────────────────────────────────────

    async def get_quote(self, symbol: str, exchange: str) -> Quote:
        from datetime import datetime
        from ..shared.market_data_cache import get_quote
        # If Yahoo can't quote right now (e.g. just after market close), fall
        # back to this position's own last real known price — never a fixed
        # ₹100 placeholder. That placeholder fabricating a fill price is what
        # caused the NSE/CRED/BLUECLOUD bug for new buys, and the exact same
        # bug for exits: real positions just got force-closed at ₹99.95 at
        # market-close square-off, recording a fake multi-thousand-rupee loss
        # on stocks worth 10-40x that. Only use 100.0 if there's truly nothing
        # better — no live quote AND no existing position to fall back to.
        key = f"{symbol}_{exchange}"
        try:
            fetched = get_quote(symbol, exchange)
        except Exception:
            fetched = None
        if fetched:
            price = Decimal(str(fetched))
        else:
            existing = self._positions.get(key)
            if existing and existing.current_price > 0:
                price = existing.current_price
                log.warning("paper_broker.quote_fallback_to_last_known",
                            symbol=symbol, price=str(price))
            else:
                raise ValueError(
                    f"No live quote and no known price for {symbol}/{exchange}. "
                    "Cannot fabricate a fill price."
                )
        now = datetime.utcnow()
        return Quote(
            symbol=symbol,
            exchange=Exchange(exchange),
            timestamp=now,
            ltp=price,
            open=price,
            high=price * Decimal("1.02"),
            low=price * Decimal("0.98"),
            close=price,
            volume=1_000_000,
        )

    async def get_order_book(self, symbol: str, exchange: str) -> OrderBook:
        from datetime import datetime
        quote = await self.get_quote(symbol, exchange)
        from ..shared.schemas import OrderBookLevel
        spread = quote.ltp * Decimal("0.001")
        return OrderBook(
            symbol=symbol,
            exchange=Exchange(exchange),
            timestamp=datetime.utcnow(),
            bids=[OrderBookLevel(price=quote.ltp - spread, quantity=1000)],
            asks=[OrderBookLevel(price=quote.ltp + spread, quantity=1000)],
        )

    async def stream_quotes(
        self, symbols: list[tuple[str, str]], callback: QuoteCallback
    ) -> None:
        self._subscriptions.append((symbols, callback))
        if self._stream_task is None or self._stream_task.done():
            self._stream_task = asyncio.create_task(self._mock_stream())

    def add_symbols(self, callback: QuoteCallback, symbols: list[tuple[str, str]]) -> None:
        """Grow an existing subscriber's watchlist at runtime — e.g. when news
        mentions a stock that wasn't being watched before."""
        for i, (existing, cb) in enumerate(self._subscriptions):
            if cb == callback:
                merged = existing + [s for s in symbols if s not in existing]
                self._subscriptions[i] = (merged, cb)
                return
        self._subscriptions.append((symbols, callback))

    async def stop_stream(self) -> None:
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
        self._subscriptions.clear()

    async def _mock_stream(self) -> None:
        """Emits random-walk quotes every second for each subscriber's own watchlist."""
        from datetime import datetime
        while True:
            for symbols, callback in list(self._subscriptions):
                for symbol, exchange in symbols:
                    if symbol not in self._prices:
                        q = await self.get_quote(symbol, exchange)
                        self._prices[symbol] = q.ltp
                    else:
                        pct = Decimal(str(random.gauss(0, 0.00005)))
                        self._prices[symbol] *= (1 + pct)
                    q = Quote(
                        symbol=symbol,
                        exchange=Exchange(exchange),
                        timestamp=datetime.utcnow(),
                        ltp=self._prices[symbol],
                        open=self._prices[symbol],
                        high=self._prices[symbol] * Decimal("1.001"),
                        low=self._prices[symbol] * Decimal("0.999"),
                        close=self._prices[symbol],
                        volume=random.randint(1000, 100_000),
                    )
                    callback(q)
            await asyncio.sleep(1.0)

    # ── Orders ─────────────────────────────────────────────────────────────

    async def place_order(self, order: Order) -> OrderResult:
        # A real broker would never invent a price for a symbol it can't quote —
        # this caught a real bug where unquotable symbols (a non-tradeable name
        # mis-extracted from news, or a real symbol Yahoo briefly failed on)
        # got "bought" at the ₹100 placeholder price. Only gate brand-new
        # positions — an already-held position can still be priced/exited even
        # if a single quote call hiccups, using its last-known price.
        pos_key = f"{order.symbol}_{order.exchange.value}"
        is_new_position = order.side == OrderSide.BUY and pos_key not in self._positions
        if is_new_position:
            from ..shared.market_data_cache import get_quote as _real_quote
            real_price = _real_quote(order.symbol, order.exchange.value)
            if real_price is None:
                log.warning("paper_broker.rejected_no_real_quote", symbol=order.symbol)
                return OrderResult(
                    order_id=order.id,
                    status=OrderStatus.REJECTED,
                    rejection_reason=f"No real market price available for {order.symbol} — refusing to trade on a placeholder price",
                )

        quote = await self.get_quote(order.symbol, order.exchange.value)
        fill_price = self._apply_slippage(
            quote.ltp, order.side, order.order_type
        )
        fill_value = fill_price * Decimal(str(order.quantity))

        realized_pnl = Decimal("0")
        tax = Decimal("0")
        charges = Decimal("0")
        entry_price: Optional[Decimal] = None
        entry_time: Optional[datetime] = None
        async with self._lock:
            if order.side == OrderSide.BUY:
                # A position's tax treatment (intraday speculative income vs.
                # STCG/LTCG) is fixed at entry — an exit later placed by
                # portfolio_manager (source_pod="portfolio_manager", no
                # source_desk) must not make a delivery position look
                # intraday, so we tag it once here and never re-derive it.
                is_intraday_leg = order.source_desk is None
                cost_estimate = estimate_trade_cost(
                    symbol=order.symbol, exchange=order.exchange, quantity=order.quantity,
                    price=fill_price, side=order.side, order_type=order.order_type,
                    is_intraday=is_intraday_leg,
                )
                charges = cost_estimate.commission
                cost = fill_value + charges
                if cost > self._balance:
                    return OrderResult(
                        order_id=order.id,
                        status=OrderStatus.REJECTED,
                        rejection_reason="Insufficient funds",
                    )
                self._balance -= cost
                self._update_position(order, fill_price, charges, is_intraday_leg)
            else:
                pos_key = f"{order.symbol}_{order.exchange.value}"
                if pos_key not in self._positions:
                    return OrderResult(
                        order_id=order.id,
                        status=OrderStatus.REJECTED,
                        rejection_reason="No position to sell",
                    )
                pos = self._positions[pos_key]
                same_source = (order.source_pod, order.source_desk) == (pos.source_pod, pos.source_desk)
                if not same_source and order.source_pod not in _CROSS_SOURCE_EXIT_ALLOWED:
                    log.warning("paper_broker.rejected_cross_source_exit", symbol=order.symbol,
                                position_source=(pos.source_pod, pos.source_desk),
                                order_source=(order.source_pod, order.source_desk))
                    return OrderResult(
                        order_id=order.id,
                        status=OrderStatus.REJECTED,
                        rejection_reason=(
                            f"Position in {order.symbol} is owned by "
                            f"{pos.source_pod or pos.source_desk or 'another strategy'} — "
                            f"refusing cross-strategy exit"
                        ),
                    )
                entry_price  = pos.average_price
                entry_time   = pos.opened_at
                qty_before   = pos.quantity
                cost_estimate = estimate_trade_cost(
                    symbol=order.symbol, exchange=order.exchange, quantity=order.quantity,
                    price=fill_price, side=order.side, order_type=order.order_type,
                    is_intraday=pos.is_intraday,
                )
                exit_charges = cost_estimate.commission
                # This leg's share of the entry-side charges paid when the
                # position was opened, so realized P&L reflects the full
                # round-trip cost, not just the exit leg.
                proportional_entry_charges = (
                    pos.entry_charges * Decimal(order.quantity) / Decimal(qty_before)
                    if qty_before else Decimal("0")
                )
                charges = exit_charges + proportional_entry_charges
                realized_pnl = (fill_price - entry_price) * Decimal(str(order.quantity)) - charges

                holding_days = (datetime.utcnow() - entry_time).days if entry_time else 0
                tax, self._ltcg_realized_this_fy = estimate_capital_gains_tax(
                    realized_pnl, pos.is_intraday, holding_days, self._ltcg_realized_this_fy,
                )

                self._balance += fill_value - exit_charges - tax
                self._close_or_reduce_position(order, fill_price, proportional_entry_charges)

        broker_order_id = f"PAPER_{uuid.uuid4().hex[:8].upper()}"
        order.broker_order_id = broker_order_id
        self._orders[broker_order_id] = order
        self._trade_book.append({
            "trade_id":    broker_order_id,
            "symbol":      order.symbol,
            "exchange":    order.exchange.value,
            "side":        order.side.value,
            "quantity":    order.quantity,
            "price":       float(fill_price),
            "entry_price": float(entry_price) if entry_price is not None else None,
            "entry_time":  entry_time.isoformat() if entry_time is not None else None,
            "pnl":         float(realized_pnl),
            "charges":     float(charges),
            "tax":         float(tax),
            "net_pnl":     float(realized_pnl - tax),
            "slippage":    float(abs(fill_price - quote.ltp) * Decimal(str(order.quantity))),
            "source_pod":  order.source_pod,
            "source_desk": order.source_desk,
            "strategy":    order.strategy,
            "timestamp":   datetime.utcnow().isoformat(),
        })
        self._save_state()

        log.debug(
            "paper.filled",
            symbol=order.symbol,
            side=order.side.value,
            qty=order.quantity,
            price=str(fill_price),
        )
        return OrderResult(
            order_id=order.id,
            broker_order_id=broker_order_id,
            status=OrderStatus.FILLED,
            filled_quantity=order.quantity,
            average_fill_price=fill_price,
        )

    async def modify_order(self, broker_order_id: str, updates: dict) -> OrderResult:
        return OrderResult(
            order_id=broker_order_id,
            broker_order_id=broker_order_id,
            status=OrderStatus.OPEN,
        )

    async def cancel_order(self, broker_order_id: str) -> bool:
        if broker_order_id in self._orders:
            del self._orders[broker_order_id]
        return True

    async def get_trade_book(self) -> list[dict]:
        return list(self._trade_book)

    async def get_order_status(self, broker_order_id: str) -> OrderResult:
        order = self._orders.get(broker_order_id)
        if not order:
            return OrderResult(order_id=broker_order_id, status=OrderStatus.EXPIRED)
        return OrderResult(
            order_id=order.id,
            broker_order_id=broker_order_id,
            status=OrderStatus.FILLED,
            filled_quantity=order.quantity,
        )

    async def get_order_book_orders(self) -> list[Order]:
        return list(self._orders.values())

    async def get_historical_data(
        self, symbol: str, exchange: str, interval: str, start: str, end: str
    ) -> list[dict]:
        from ..shared.market_data_cache import download as _cached_download
        suffix = ".NS" if exchange == "NSE" else ".BO"
        yf_interval = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "60m", "1d": "1d"}.get(
            interval, "1d"
        )
        df = _cached_download(f"{symbol}{suffix}", interval=yf_interval, start=start, end=end)
        if df is None or df.empty:
            return []
        return [
            {
                "timestamp": str(idx),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row["Volume"]),
            }
            for idx, row in df.iterrows()
        ]

    # ── Helpers ────────────────────────────────────────────────────────────

    def _apply_slippage(
        self, price: Decimal, side: OrderSide, order_type: OrderType
    ) -> Decimal:
        if order_type == OrderType.MARKET:
            slip = price * self._slippage_bps / Decimal("10000")
            return price + slip if side == OrderSide.BUY else price - slip
        return price

    def _update_position(
        self, order: Order, fill_price: Decimal, charges: Decimal = Decimal("0"),
        is_intraday: bool = True,
    ) -> None:
        key = f"{order.symbol}_{order.exchange.value}"
        if key in self._positions:
            pos = self._positions[key]
            total_qty = pos.quantity + order.quantity
            avg = (pos.average_price * pos.quantity + fill_price * order.quantity) / total_qty
            self._positions[key] = pos.model_copy(update={
                "quantity": total_qty,
                "average_price": avg,
                "current_price": fill_price,
                "source_pod": order.source_pod,
                "entry_charges": pos.entry_charges + charges,
                # Refresh the exit plan to the latest signal's targets — otherwise a
                # position opened before this feature existed (stop_loss=None) would
                # stay unmonitored forever even after averaging into it again.
                "stop_loss": order.stop_loss or pos.stop_loss,
                "take_profit": order.take_profit or pos.take_profit,
                "max_hold_until": order.max_hold_until or pos.max_hold_until,
            })
        else:
            self._positions[key] = Position(
                symbol=order.symbol,
                exchange=order.exchange,
                quantity=order.quantity,
                average_price=fill_price,
                current_price=fill_price,
                side=order.side,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit,
                max_hold_until=order.max_hold_until,
                source_pod=order.source_pod,
                source_desk=order.source_desk,
                strategy=order.strategy,
                is_intraday=is_intraday,
                entry_charges=charges,
            )

    def _close_or_reduce_position(
        self, order: Order, fill_price: Decimal, proportional_entry_charges: Decimal = Decimal("0"),
    ) -> None:
        key = f"{order.symbol}_{order.exchange.value}"
        pos = self._positions[key]
        if order.quantity >= pos.quantity:
            del self._positions[key]
        else:
            self._positions[key] = pos.model_copy(update={
                "quantity": pos.quantity - order.quantity,
                "current_price": fill_price,
                "entry_charges": pos.entry_charges - proportional_entry_charges,
            })

    async def mark_to_market(self) -> None:
        """Refresh current_price for every open position from a live quote.
        Without this, current_price is only ever set at fill time and never
        touched again — unrealized P&L (computed from current_price) would
        stay frozen at its entry-time value no matter how far the market moves."""
        from ..shared.market_data_cache import get_quote as _real_quote
        async with self._lock:
            for key, pos in list(self._positions.items()):
                price = _real_quote(pos.symbol, pos.exchange.value)
                if price is not None:
                    self._positions[key] = pos.model_copy(update={
                        "current_price": Decimal(str(price)),
                    })
            self._save_state()

    async def purge_position(self, symbol: str, exchange: str) -> Optional[Position]:
        """Admin-only: forcibly remove a position with no real market price
        (e.g. a hallucinated/invalid symbol bought before validation existed).
        Not a sale — there's no real fill to record, just void the position and
        refund the cash that was debited for it (otherwise the account balance
        stays permanently short by the value of a trade that never really happened)."""
        key = f"{symbol}_{exchange}"
        async with self._lock:
            pos = self._positions.pop(key, None)
            if pos:
                self._balance += pos.average_price * pos.quantity + pos.entry_charges
                self._save_state()
        return pos
