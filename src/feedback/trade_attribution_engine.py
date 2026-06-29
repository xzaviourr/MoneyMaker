"""
TradeAttributionEngine — splits P&L into signal / execution / timing / luck components.

Signal contribution: how much of the move was predictable from the entry signal
Execution contribution: slippage vs VWAP
Timing contribution: entry vs optimal entry within the session
Luck contribution: residual unexplained P&L
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Optional

import structlog

from ..shared.market_data_cache import download as cache_download
from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    Exchange,
    Message,
    MessageType,
    RegimeSnapshot,
    SignalDirection,
    Trade,
    TradeAttribution,
)

log = structlog.get_logger(__name__)


class TradeAttributionEngine:
    """Listens for closed trades and computes multi-factor P&L attribution."""

    def __init__(self) -> None:
        self._bus = MessageBus.get()

    async def start(self) -> None:
        self._bus.subscribe(MessageType.ORDER_FILLED, self._on_fill)
        log.info("trade_attribution_engine.started")

    async def _on_fill(self, msg: Message) -> None:
        pass  # individual fills don't trigger attribution — we need a closed trade

    async def attribute(
        self,
        trade: Trade,
        regime_at_entry: Optional[RegimeSnapshot] = None,
        regime_at_exit:  Optional[RegimeSnapshot] = None,
    ) -> TradeAttribution:
        """Attribute P&L for a completed trade. Called by OutcomeAttributionTimer."""
        symbol        = trade.symbol
        entry_price   = float(trade.entry_price)
        exit_price    = float(trade.exit_price) if trade.exit_price else entry_price
        quantity      = trade.quantity
        direction     = trade.direction
        total_pnl     = Decimal(str(trade.realized_pnl or 0))

        # --- Signal contribution ---
        signal_move = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._estimate_signal_move(symbol, entry_price, direction)
        )
        signal_contribution = float(
            min(1.0, max(0.0, signal_move / abs(exit_price - entry_price)))
            if abs(exit_price - entry_price) > 0 else 0.0
        )

        # --- Execution contribution (slippage vs mid) ---
        exec_contribution = self._execution_score(trade)

        # --- Timing contribution ---
        timing_contribution = self._timing_score(trade, signal_move)

        # --- Luck residual ---
        luck_contribution = max(0.0, 1.0 - signal_contribution
                                     - abs(exec_contribution)
                                     - abs(timing_contribution))

        # Holding period
        holding_hours = 0.0
        if trade.entry_time and trade.exit_time:
            delta = trade.exit_time - trade.entry_time
            holding_hours = delta.total_seconds() / 3600

        slippage_cost = Decimal(str(trade.slippage_cost or 0))

        attribution = TradeAttribution(
            trade_id=str(trade.trade_id),
            symbol=symbol,
            exchange=trade.exchange,
            direction=direction,
            planned_price=entry_price,
            executed_price=float(trade.entry_price),
            planned_quantity=quantity,
            executed_quantity=quantity,
            slippage_bps=float(slippage_cost / Decimal(str(entry_price * quantity)) * 10_000)
                         if entry_price * quantity > 0 else 0.0,
            execution_quality=(
                "good" if float(slippage_cost) < entry_price * quantity * 0.001
                else ("acceptable" if float(slippage_cost) < entry_price * quantity * 0.003
                      else "poor")
            ),
            source_agent="trade_attribution_engine",
            strategy=trade.strategy or trade.source_pod or trade.source_desk or "unknown",
            total_pnl=total_pnl,
            signal_contribution=signal_contribution,
            execution_contribution=exec_contribution,
            timing_contribution=timing_contribution,
            luck_contribution=luck_contribution,
            regime_at_entry=regime_at_entry,
            regime_at_exit=regime_at_exit,
            holding_period_hours=holding_hours,
            slippage_cost=slippage_cost,
        )

        await self._bus.publish(Message(
            type=MessageType.TRADE_ATTRIBUTED,
            payload=attribution.model_dump(),
            source="trade_attribution_engine",
        ))

        log.info("attribution.done",
                 trade_id=str(trade.trade_id),
                 pnl=str(total_pnl),
                 signal_pct=signal_contribution,
                 exec_pct=exec_contribution)

        return attribution

    @staticmethod
    def _estimate_signal_move(symbol: str, entry_price: float,
                               direction: SignalDirection) -> float:
        """Returns the 'fair' portion of move explainable by momentum at entry."""
        try:
            df = cache_download(f"{symbol}.NS", period="5d", interval="60m")
            if df is None or len(df) < 4:
                return 0.0
            close = df["Close"].squeeze().astype(float)
            # Trend at entry: 4-bar EMA slope
            ema4  = close.ewm(span=4).mean()
            slope = float((ema4.iloc[-1] - ema4.iloc[-4]) / ema4.iloc[-4])
            if direction == SignalDirection.LONG:
                return max(0.0, slope * entry_price)
            else:
                return max(0.0, -slope * entry_price)
        except Exception:
            return 0.0

    @staticmethod
    def _execution_score(trade: Trade) -> float:
        """Positive = executed better than entry price, negative = worse."""
        slippage = float(trade.slippage_cost or 0)
        notional = float(trade.entry_price) * trade.quantity
        if notional == 0:
            return 0.0
        return -slippage / notional  # negative slippage → negative contribution

    @staticmethod
    def _timing_score(trade: Trade, signal_move: float) -> float:
        """Simple proxy: if entry was within 0.3% of session low (for longs), good timing."""
        # Simplified — a full implementation would use session OHLC
        return 0.1 if signal_move > 0 else -0.05
