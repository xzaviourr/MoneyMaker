"""ExecutionTrader — Places the actual orders; handles slicing, VWAP, retries, and confirmation."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog

from ...brokers.broker_gateway import BrokerGateway
from ...shared.message_bus import MessageBus
from ...shared.schemas import (
    AllocationPlan,
    ExecutionPlan,
    Message,
    MessageType,
    Order,
    OrderSide,
    OrderType,
    SignalDirection,
)

log = structlog.get_logger(__name__)


class ExecutionTrader:
    agent_id = "room3.execution_trader"

    async def execute(
        self,
        plan: AllocationPlan,
        timing: dict[str, Any],
        risk_check: dict[str, Any],
        tail_check: dict[str, Any],
    ) -> ExecutionPlan:
        # Bail if any gate blocked
        if not risk_check.get("passed", False):
            return ExecutionPlan(
                allocation_plan=plan,
                status="blocked",
                reason=f"risk_gate: {risk_check.get('issues', [])}",
                orders_placed=[],
            )

        if not tail_check.get("passed", False):
            return ExecutionPlan(
                allocation_plan=plan,
                status="blocked",
                reason=f"tail_risk: {tail_check.get('warnings', [])}",
                orders_placed=[],
            )

        advice = timing.get("execution_advice", "immediate")
        if advice in ("wait_tomorrow_open", "wait_30min"):
            return ExecutionPlan(
                allocation_plan=plan,
                status="deferred",
                reason=f"timing: {timing.get('reason', '')}",
                orders_placed=[],
                defer_to=advice,
            )

        broker = BrokerGateway.get()
        side   = (OrderSide.BUY if plan.direction == SignalDirection.LONG
                  else OrderSide.SELL)

        # Exit plan — turn Room 1's debated target/stop/horizon into actual
        # price levels so the position closes itself instead of being held
        # forever. Long positions only (mirrors the rest of this desk).
        stop_loss_price = take_profit_price = None
        max_hold_until  = None
        if side == OrderSide.BUY:
            quote = await broker.get_quote(plan.symbol, plan.exchange.value)
            entry_est = quote.ltp
            stop_loss_price   = entry_est * (1 - Decimal(str(plan.stop_loss_pct_downside)) / 100)
            take_profit_price = entry_est * (1 + Decimal(str(plan.target_pct_upside)) / 100)
            max_hold_until    = datetime.utcnow() + timedelta(weeks=plan.time_horizon_weeks)

        # Slice if > 2% ADV
        pct_of_adv = float(timing.get("pct_of_adv", 0))
        slices     = 1 if pct_of_adv < 1.0 else (2 if pct_of_adv < 5.0 else 3)
        slice_qty  = plan.quantity // slices
        orders     = []

        for i in range(slices):
            qty = slice_qty if i < slices - 1 else plan.quantity - slice_qty * (slices - 1)
            if qty <= 0:
                continue
            try:
                order = Order(
                    symbol=plan.symbol,
                    exchange=plan.exchange,
                    side=side,
                    order_type=OrderType.MARKET,
                    quantity=qty,
                    stop_loss=stop_loss_price,
                    take_profit=take_profit_price,
                    max_hold_until=max_hold_until,
                    source_desk="long_term_desk",
                    strategy="long_term_idea",
                )
                result = await broker.place_order(order)
                orders.append(result.model_dump(mode="json"))
                log.info("execution_trader.order_placed",
                         symbol=plan.symbol, qty=qty, slice=i+1, status=result.status,
                         stop_loss=str(stop_loss_price), take_profit=str(take_profit_price),
                         max_hold_until=str(max_hold_until))

                if slices > 1 and i < slices - 1:
                    await asyncio.sleep(60)  # 1-min gap between slices
            except Exception as e:
                log.error("execution_trader.order_failed",
                          symbol=plan.symbol, slice=i+1, error=str(e))
                orders.append({"error": str(e), "qty": qty})

        exec_plan = ExecutionPlan(
            allocation_plan=plan,
            status="executed" if any("order_id" in o for o in orders) else "partial",
            reason="",
            orders_placed=orders,
        )

        bus = MessageBus.get()
        await bus.publish(Message(
            type=MessageType.LT_EXECUTION_COMPLETE,
            payload=exec_plan.model_dump(),
            source=self.agent_id,
        ))

        return exec_plan
