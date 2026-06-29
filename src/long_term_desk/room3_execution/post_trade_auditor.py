"""PostTradeAuditor — Verifies fills, computes slippage vs estimate, logs to TradeAttribution."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from ...intelligence.explainability_ledger import ExplainabilityLedger
from ...shared.message_bus import MessageBus
from ...shared.schemas import (
    AllocationPlan,
    ExecutionPlan,
    Message,
    MessageType,
    TradeAttribution,
)

log = structlog.get_logger(__name__)


class PostTradeAuditor:
    agent_id = "room3.post_trade_auditor"

    async def audit(
        self,
        plan: AllocationPlan,
        exec_plan: ExecutionPlan,
        expected_price: float,
    ) -> dict[str, Any]:
        if exec_plan.status in ("blocked", "deferred"):
            log.info("post_trade_auditor.skipped",
                     symbol=plan.symbol, reason=exec_plan.status)
            return {"audited": False, "reason": exec_plan.status}

        orders_placed = exec_plan.orders_placed or []
        filled_orders = [o for o in orders_placed if "order_id" in o]
        failed_orders = [o for o in orders_placed if "error" in o]

        # Compute average fill price
        total_qty   = sum(int(o.get("quantity", plan.quantity // len(filled_orders)))
                          for o in filled_orders) if filled_orders else 0
        fill_prices = [float(o.get("average_fill_price", expected_price))
                       for o in filled_orders if "average_fill_price" in o]
        avg_fill    = (sum(fill_prices) / len(fill_prices)) if fill_prices else expected_price

        slippage_bps = abs(avg_fill - expected_price) / expected_price * 10_000

        attribution = TradeAttribution(
            symbol=plan.symbol,
            exchange=plan.exchange,
            direction=plan.direction,
            planned_price=expected_price,
            executed_price=avg_fill,
            planned_quantity=plan.quantity,
            executed_quantity=total_qty,
            slippage_bps=slippage_bps,
            execution_quality="good" if slippage_bps < 10 else
                              ("acceptable" if slippage_bps < 30 else "poor"),
            failed_slices=len(failed_orders),
            source_agent=self.agent_id,
            timestamp=datetime.utcnow(),
        )

        bus = MessageBus.get()
        await bus.publish(Message(
            type=MessageType.TRADE_ATTRIBUTION,
            payload=attribution.model_dump(),
            source=self.agent_id,
        ))

        log.info("post_trade_auditor.done",
                 symbol=plan.symbol,
                 slippage_bps=slippage_bps,
                 quality=attribution.execution_quality,
                 failed_slices=len(failed_orders))

        # Make the decision-time price vs the actual fill price visible side by
        # side — this was previously computed (slippage_bps) but never shown
        # anywhere, so "we decided to buy at ~102, why did it fill at 103?"
        # had no visible answer.
        await ExplainabilityLedger.get().record(
            agent_id=self.agent_id,
            decision="execution_audit",
            reasoning=f"Decided at ~₹{expected_price:.2f}, filled at ₹{avg_fill:.2f} "
                      f"({slippage_bps:.1f} bps slippage, {attribution.execution_quality})",
            symbol=plan.symbol,
            inputs={"planned_price": expected_price, "planned_quantity": plan.quantity},
            outputs={"executed_price": avg_fill, "executed_quantity": total_qty,
                      "slippage_bps": slippage_bps},
        )

        return {
            "audited":          True,
            "avg_fill_price":   avg_fill,
            "slippage_bps":     slippage_bps,
            "execution_quality": attribution.execution_quality,
            "failed_slices":    len(failed_orders),
            "filled_qty":       total_qty,
        }
