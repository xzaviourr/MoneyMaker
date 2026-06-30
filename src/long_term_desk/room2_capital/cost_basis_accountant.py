"""CostBasisAccountant — Computes all-in costs: STT, brokerage, slippage, impact; adjusts conviction."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import structlog

from ...shared.schemas import Exchange, IdeaVerdict, OrderSide
from ...shared.trade_cost_estimator import estimate_trade_cost

log = structlog.get_logger(__name__)


class CostBasisAccountant:
    agent_id = "room2.cost_basis_accountant"

    async def compute(
        self,
        verdict: IdeaVerdict,
        position_size: dict[str, Any],
        current_price: float,
    ) -> dict[str, Any]:
        quantity       = int(position_size.get("quantity", 0))
        position_value = float(position_size.get("position_value_inr", 0))

        if quantity == 0 or position_value == 0:
            return {"viable": False, "reason": "zero_quantity"}

        side = OrderSide.BUY if verdict.direction.value == "long" else OrderSide.SELL

        try:
            cost = estimate_trade_cost(
                symbol=verdict.symbol,
                exchange=verdict.exchange,
                quantity=quantity,
                price=Decimal(str(current_price)),
                is_intraday=False,  # long-term desk always delivery
                is_short=(side == OrderSide.SELL),
            )
        except Exception as e:
            log.warning("cost_accountant.estimate_failed", error=str(e))
            return {"viable": True, "total_cost_inr": 0.0, "breakeven_pct": 0.0}

        total_cost = float(cost.total_cost)
        breakeven  = total_cost / position_value * 100 if position_value > 0 else 0

        # Trade is not viable if breakeven exceeds 1% (costs eat into edge)
        viable = breakeven < 1.0

        log.info("cost_accountant.done",
                 symbol=verdict.symbol,
                 total_cost=total_cost,
                 breakeven_pct=breakeven,
                 viable=viable)

        return {
            "viable":            viable,
            "total_cost_inr":    total_cost,
            "breakeven_pct":     breakeven,
            "commission":        float(cost.commission),
            "spread_cost":       float(cost.spread_cost),
            "market_impact":     float(cost.market_impact),
            "slippage_estimate": float(cost.slippage),
        }
