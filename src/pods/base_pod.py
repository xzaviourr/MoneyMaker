"""
BasePod — abstract base class for all intraday trading pods.

Each pod has:
  - StrategyAgent (signal generation)
  - RiskAgent (pod-level risk)
  - ExecutionAgent (order routing)
  - PodMemory (local state)

The tight signal → risk-check → execute loop runs with no LLM for FAST path.
"""
from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

import structlog

from ..brokers.broker_gateway import BrokerGateway
from ..foundation.regime_classifier import RegimeClassifier
from ..shared.config import toml_cfg
from ..shared.market_hours import is_market_open
from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    Message,
    MessageType,
    Order,
    OrderSide,
    OrderType,
    PodConfig,
    PodMetrics,
    PodState,
    Position,
    Quote,
    TradeSignal,
)
from ..shared.trade_cost_estimator import estimate_trade_cost, trade_has_edge

log = structlog.get_logger(__name__)


class BasePod(ABC):
    """
    Subclass and implement generate_signal().
    Everything else (risk checks, execution, P&L tracking) is handled here.
    """

    def __init__(self, config: PodConfig, gateway: BrokerGateway) -> None:
        self.config     = config
        self._gateway   = gateway
        self._bus       = MessageBus.get()
        self._positions: dict[str, Position] = {}
        self._daily_pnl  = Decimal("0")
        self._total_pnl  = Decimal("0")
        self._trade_count = 0  # entries placed — not the same as closed trades, see get_metrics()
        self._win_count  = 0
        self._loss_count = 0
        # Must match PaperBroker's own commission exactly, or a trade that's
        # marginally profitable before commission gets counted here as a win
        # while the broker (and the Feedback page, fed from the broker's own
        # trade book) correctly counts it as a net loss — which is exactly
        # the Pods-page-vs-Feedback-page mismatch this was causing.
        self._commission = Decimal(str(
            toml_cfg.get("broker", {}).get("paper", {}).get("commission_flat", 20.0)
        ))
        self._is_paused  = False
        self._signal_cooldown: dict[str, datetime] = {}  # symbol -> retry-after, set on order rejection
        self._task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
        self._metrics    = PodMetrics(pod_id=self.pod_id)
        self._regime_classifier: Optional[RegimeClassifier] = None
        self._load_metrics()  # without this, every restart silently resets trade count/win rate/P&L to zero

    # ── Metrics persistence ──────────────────────────────────────────────────
    # In-memory counters alone reset to zero on every restart even though the
    # broker's own trade history survives — the Pods page would show 0/0/0%
    # forever in a dev environment that restarts often.

    def _metrics_path(self) -> Path:
        return Path(f"data/pod_metrics_{self.pod_id}.json")

    def _load_metrics(self) -> None:
        path = self._metrics_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            self._total_pnl   = Decimal(data.get("total_pnl", "0"))
            self._daily_pnl   = Decimal(data.get("daily_pnl", "0"))
            self._trade_count = int(data.get("trade_count", 0))
            self._win_count   = int(data.get("win_count", 0))
            self._loss_count  = int(data.get("loss_count", 0))
        except Exception:
            log.exception("pod.metrics_load_failed", pod_id=self.pod_id)

    def _save_metrics(self) -> None:
        try:
            import os
            path = self._metrics_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "total_pnl":   str(self._total_pnl),
                "daily_pnl":   str(self._daily_pnl),
                "trade_count": self._trade_count,
                "loss_count":  self._loss_count,
                "win_count":   self._win_count,
            }))
            os.replace(tmp, path)
        except Exception:
            log.exception("pod.metrics_save_failed", pod_id=self.pod_id)

    # ── Identity ───────────────────────────────────────────────────────────

    @property
    def pod_id(self) -> str:
        return self.config.pod_id

    @property
    def state(self) -> PodState:
        return self.config.state

    # ── Abstract interface ─────────────────────────────────────────────────

    @abstractmethod
    async def generate_signal(self, quote: Quote) -> Optional[TradeSignal]:
        """Return a signal or None. Must be fast (< 500ms)."""

    @abstractmethod
    def watchlist(self) -> list[tuple[str, str]]:
        """Return list of (symbol, exchange) pairs this pod monitors."""

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self, regime_classifier: Optional[RegimeClassifier] = None) -> None:
        self._regime_classifier = regime_classifier
        await self._reconcile_with_broker()
        symbols = self.watchlist()
        await self._gateway.stream_quotes(symbols, self._on_quote)
        # Catch positions closed by PositionMonitor — it bypasses _exit_position()
        # so win/loss counters would never update without this subscriber.
        self._bus.subscribe(MessageType.ORDER_FILLED, self._on_external_fill)
        log.info("pod.started", pod_id=self.pod_id, symbols=[s for s, _ in symbols])

    async def _reconcile_with_broker(self) -> None:
        """On restart: reload any open positions that belong to this pod from the broker.
        Without this, a system crash while trades are open leaves the pod blind to its
        own holdings — every subsequent tick would skip exit checks, and stop-losses
        and take-profits would never fire."""
        try:
            broker_positions = await self._gateway.get_positions()
        except Exception as exc:
            log.warning("pod.reconcile_failed", pod_id=self.pod_id, error=str(exc))
            return
        restored = 0
        for pos in broker_positions:
            if pos.source_pod != self.pod_id:
                continue
            key = f"{pos.symbol}_{pos.exchange.value}"
            if key not in self._positions:
                self._positions[key] = pos
                restored += 1
        if restored:
            log.info("pod.reconciled", pod_id=self.pod_id, restored=restored)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        await self._close_all_positions()
        log.info("pod.stopped", pod_id=self.pod_id)

    async def pause(self, reason: str = "") -> None:
        self._is_paused = True
        log.info("pod.paused", pod_id=self.pod_id, reason=reason)
        await self._bus.publish(
            Message(type=MessageType.POD_PAUSED, source=self.pod_id,
                    payload={"reason": reason})
        )

    async def resume(self) -> None:
        self._is_paused = False
        log.info("pod.resumed", pod_id=self.pod_id)
        await self._bus.publish(
            Message(type=MessageType.POD_RESUMED, source=self.pod_id, payload={})
        )

    # ── Quote handler ──────────────────────────────────────────────────────

    def _on_quote(self, quote: Quote) -> None:
        """Called synchronously by the broker stream callback."""
        asyncio.create_task(self._process_quote(quote))

    async def _process_quote(self, quote: Quote) -> None:
        if self._is_paused:
            return
        if self.state in (PodState.KILLED, PodState.REVIEW):
            return

        # Update position prices
        key = f"{quote.symbol}_{quote.exchange.value}"
        if key in self._positions:
            pos = self._positions[key]
            pnl = (quote.ltp - pos.average_price) * pos.quantity
            self._positions[key] = pos.model_copy(update={
                "current_price": quote.ltp,
                "unrealized_pnl": pnl,
            })
            # Check stop-loss / take-profit / max holding time
            if pos.stop_loss and quote.ltp <= pos.stop_loss:
                await self._exit_position(pos, quote,
                    reason=f"Stop-loss hit — price ₹{float(quote.ltp):.2f} fell to stop ₹{float(pos.stop_loss):.2f}")
                return
            if pos.take_profit and quote.ltp >= pos.take_profit:
                await self._exit_position(pos, quote,
                    reason=f"Take-profit hit — price ₹{float(quote.ltp):.2f} reached target ₹{float(pos.take_profit):.2f}")
                return
            if pos.max_hold_until and datetime.utcnow() >= pos.max_hold_until:
                await self._exit_position(pos, quote,
                    reason="Maximum holding time reached — intraday square-off")
                return
            if not is_market_open():
                await self._exit_position(pos, quote,
                    reason="Market closing — intraday square-off")
                return

        # Intraday pods only open new positions during NSE hours (09:15-15:30 IST)
        if not is_market_open():
            return

        # Check regime compatibility
        if self._regime_classifier and self.config.compatible_regimes:
            if not self._regime_classifier.is_regime_compatible(self.config.compatible_regimes):
                return

        # Generate signal
        try:
            signal = await self.generate_signal(quote)
        except Exception as exc:
            log.error("pod.signal_error", pod_id=self.pod_id, error=str(exc))
            return

        if signal is None or signal.is_expired:
            return

        await self._handle_signal(signal, quote)

    # ── Signal → Risk → Execute ────────────────────────────────────────────

    async def _handle_signal(self, signal: TradeSignal, quote: Quote) -> None:
        # Skip symbols that were just rejected (e.g. insufficient funds) instead of
        # re-attempting the identical order every tick — was flooding logs/broker.
        cooldown_until = self._signal_cooldown.get(signal.symbol)
        if cooldown_until and datetime.utcnow() < cooldown_until:
            return

        # Already holding a position from this same signal — let it ride to its
        # stop/target/time-exit instead of re-buying/re-selling into it every tick
        # the entry condition still holds (was firing several fills a second).
        key = f"{signal.symbol}_{signal.exchange.value}"
        if key in self._positions:
            return

        # Risk: check daily drawdown limit
        if not self._risk_check(signal, quote):
            return

        # Cost check: is edge > total cost?
        if not trade_has_edge(
            expected_edge_pct=float(signal.conviction * 2),
            order=Order(
                symbol=signal.symbol,
                exchange=signal.exchange,
                side=OrderSide.BUY if signal.direction.value == "long" else OrderSide.SELL,
                order_type=OrderType.LIMIT,
                quantity=self._compute_quantity(signal, quote),
            ),
            price=quote.ltp,
            is_intraday=True,
        ):
            log.debug("pod.no_edge", pod_id=self.pod_id, symbol=signal.symbol)
            return

        await self._execute_signal(signal, quote)

        # Publish signal event
        await self._bus.publish(
            Message(
                type=MessageType.POD_SIGNAL,
                source=self.pod_id,
                payload=signal.model_dump(mode="json"),
            )
        )

    def _risk_check(self, signal: TradeSignal, quote: Quote) -> bool:
        # Daily drawdown guard
        if self.config.capital_budget > 0:
            drawdown_pct = float(
                abs(self._daily_pnl) / self.config.capital_budget * 100
            )
            if self._daily_pnl < 0 and drawdown_pct >= self.config.max_daily_drawdown_pct:
                log.warning(
                    "pod.daily_drawdown_breach",
                    pod_id=self.pod_id,
                    drawdown_pct=drawdown_pct,
                )
                return False

        # Max open positions
        if len(self._positions) >= self.config.max_open_positions:
            return False

        return True

    async def _execute_signal(self, signal: TradeSignal, quote: Quote) -> None:
        qty = self._compute_quantity(signal, quote)
        if qty <= 0:
            log.debug(
                "pod.skipped_no_capital",
                pod_id=self.pod_id,
                symbol=signal.symbol,
                available=str(self._available_capital()),
            )
            return

        side = OrderSide.BUY if signal.direction.value == "long" else OrderSide.SELL
        sl_price = signal.stop_loss
        tp_price = signal.take_profit
        max_hold_until = datetime.utcnow() + timedelta(minutes=self.config.max_holding_minutes)

        # Fall back to config-pct defaults off the signal-time quote (not the fill
        # price, which isn't known yet) so the order — and the broker's resulting
        # Position — carry the same exit plan the pod will itself monitor below.
        if not sl_price and side == OrderSide.BUY:
            sl_price = quote.ltp * Decimal(str(1 - self.config.stop_loss_pct / 100))
        if not tp_price and side == OrderSide.BUY:
            tp_price = quote.ltp * Decimal(str(1 + self.config.take_profit_pct / 100))

        order = Order(
            symbol=signal.symbol,
            exchange=signal.exchange,
            side=side,
            order_type=OrderType.LIMIT,
            quantity=qty,
            price=quote.ltp,
            stop_loss=sl_price,
            take_profit=tp_price,
            max_hold_until=max_hold_until,
            source_pod=self.pod_id,
            strategy=self.config.strategy,
            rationale=signal.rationale,
        )

        result = await self._gateway.place_order(order)
        if not result.average_fill_price:
            # e.g. "Insufficient funds" — the pod's own budget check passed but the
            # shared account balance is too low; don't hammer the broker every tick.
            self._signal_cooldown[signal.symbol] = datetime.utcnow() + timedelta(minutes=2)
            log.debug("pod.order_rejected", pod_id=self.pod_id, symbol=signal.symbol,
                      reason=result.rejection_reason)
            return
        if result.average_fill_price:
            pos = Position(
                symbol=signal.symbol,
                exchange=signal.exchange,
                quantity=qty,
                average_price=result.average_fill_price,
                current_price=result.average_fill_price,
                side=side,
                stop_loss=sl_price,
                take_profit=tp_price,
                max_hold_until=max_hold_until,
                source_pod=self.pod_id,
                strategy=self.config.strategy,
            )
            key = f"{signal.symbol}_{signal.exchange.value}"
            self._positions[key] = pos
            self._trade_count += 1
            self._save_metrics()
            log.info(
                "pod.executed",
                pod_id=self.pod_id,
                symbol=signal.symbol,
                side=side.value,
                qty=qty,
                price=str(result.average_fill_price),
            )

            from ..intelligence.explainability_ledger import ExplainabilityLedger
            await ExplainabilityLedger.get().record(
                agent_id=self.pod_id,
                decision=side.value,
                reasoning=signal.rationale,
                symbol=signal.symbol,
                inputs={"conviction": signal.conviction, "strategy": self.config.strategy},
                outputs={"quantity": qty, "fill_price": str(result.average_fill_price)},
            )

    def _deployed_capital(self) -> Decimal:
        """Capital currently tied up in this pod's open positions."""
        return sum(
            (pos.average_price * pos.quantity for pos in self._positions.values()),
            Decimal("0"),
        )

    def _available_capital(self) -> Decimal:
        """What this pod can still spend right now: budget minus what's deployed."""
        return max(Decimal("0"), self.config.capital_budget - self._deployed_capital())

    def _compute_quantity(self, signal: TradeSignal, quote: Quote) -> int:
        """Kelly-like position sizing, capped by max_position_size_pct AND
        by how much capital this pod actually has free right now."""
        available = self._available_capital()
        if available <= 0 or quote.ltp <= 0:
            return 0
        max_value_pod = min(
            self.config.capital_budget * Decimal(str(self.config.max_position_size_pct / 100)),
            available,
        )
        # Hard global cap: never exceed 2% of total account capital in a single trade,
        # regardless of pod sizing config.
        _total = Decimal(str(toml_cfg.get("capital", {}).get("total_capital", 1_000_000)))
        _max_pct = Decimal(str(toml_cfg.get("guardian", {}).get("max_position_pct_of_total_capital", 2.0)))
        max_value_global = _total * _max_pct / Decimal("100")
        max_value = min(max_value_pod, max_value_global)
        # Scale by conviction
        target_value = max_value * Decimal(str(signal.conviction))
        # Size against a price slightly worse than the signal quote — the fill
        # happens moments later and the market can move against us by then.
        buffer_price = quote.ltp * Decimal("1.005")
        qty = int(target_value / buffer_price)
        return max(0, qty)

    async def _exit_position(self, pos: Position, quote: Quote, reason: str = "manual") -> None:
        """Close a position immediately — stop-loss, take-profit, max holding time, or shutdown."""
        order = Order(
            symbol=pos.symbol,
            exchange=pos.exchange,
            side=OrderSide.SELL if pos.side == OrderSide.BUY else OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=pos.quantity,
            source_pod=self.pod_id,
        )
        key = f"{pos.symbol}_{pos.exchange.value}"
        result = await self._gateway.place_order(order)
        if not result.average_fill_price:
            # The broker has no matching position either (e.g. PositionMonitor's
            # own periodic check already closed it first) — retrying every tick
            # forever was the bug; drop our stale local copy instead.
            self._positions.pop(key, None)
            log.warning("pod.exit_failed_dropping_stale_position", pod_id=self.pod_id,
                        symbol=pos.symbol, reason=reason, rejection=result.rejection_reason)
            return
        if result.average_fill_price:
            realized_pnl = (result.average_fill_price - pos.average_price) * pos.quantity - self._commission
            self._daily_pnl  += realized_pnl
            self._total_pnl  += realized_pnl
            if realized_pnl > 0:
                self._win_count += 1
            else:
                self._loss_count += 1
            self._save_metrics()
            self._positions.pop(key, None)
            log.warning(
                "pod.position_exited",
                pod_id=self.pod_id,
                symbol=pos.symbol,
                reason=reason,
                pnl=str(realized_pnl),
            )
            from ..intelligence.explainability_ledger import ExplainabilityLedger
            await ExplainabilityLedger.get().record(
                agent_id=self.pod_id,
                decision=order.side.value,
                reasoning=reason,
                symbol=pos.symbol,
                inputs={"entry_price": str(pos.average_price), "held_since": str(pos.opened_at)},
                outputs={"quantity": pos.quantity, "fill_price": str(result.average_fill_price),
                         "pnl": str(realized_pnl)},
            )

    async def _on_external_fill(self, msg: Message) -> None:
        """Handles ORDER_FILLED published by PositionMonitor or Portfolio Manager —
        any code path that closes a pod position WITHOUT going through _exit_position().
        If _exit_position() already handled the fill, the position will already be
        gone from self._positions so this is a safe no-op in that case."""
        payload = msg.payload or {}
        order   = payload.get("order", {})
        result  = payload.get("result", {})

        if order.get("source_pod") != self.pod_id:
            return
        if order.get("side") != "sell":
            return

        symbol   = order.get("symbol", "")
        exchange = order.get("exchange", "NSE")
        key      = f"{symbol}_{exchange}"

        pos = self._positions.get(key)
        if pos is None:
            return  # pod's own _exit_position() already handled and removed it

        fill_price = result.get("average_fill_price")
        if not fill_price:
            self._positions.pop(key, None)
            return

        realized_pnl = (Decimal(str(fill_price)) - pos.average_price) * pos.quantity - self._commission
        self._daily_pnl += realized_pnl
        self._total_pnl += realized_pnl
        if realized_pnl > 0:
            self._win_count += 1
        else:
            self._loss_count += 1
        self._save_metrics()
        self._positions.pop(key, None)
        log.info(
            "pod.position_closed_externally",
            pod_id  = self.pod_id,
            symbol  = symbol,
            pnl     = str(realized_pnl),
        )

    async def _close_all_positions(self) -> None:
        for key, pos in list(self._positions.items()):
            quote = await self._gateway.get_quote(pos.symbol, pos.exchange.value)
            await self._exit_position(pos, quote, reason="pod_shutdown")

    # ── Metrics snapshot ───────────────────────────────────────────────────

    def get_metrics(self) -> PodMetrics:
        # total_trades here means *closed, completed* trades (win+loss) — the
        # only thing a win rate can sensibly be measured against. It used to
        # be the count of entries placed, which includes still-open positions
        # that haven't won or lost anything yet, silently understating the
        # real win rate.
        closed = self._win_count + self._loss_count
        win_rate = self._win_count / closed if closed > 0 else 0.0
        self._metrics = PodMetrics(
            pod_id=self.pod_id,
            total_trades=closed,
            winning_trades=self._win_count,
            losing_trades=self._loss_count,
            total_pnl=self._total_pnl,
            daily_pnl=self._daily_pnl,
            win_rate=win_rate,
        )
        return self._metrics

    def reset_daily_pnl(self) -> None:
        self._daily_pnl = Decimal("0")
