"""AllocationChair — Produces final AllocationPlan; commits capital reservation in ledger."""
from __future__ import annotations

from typing import Any

import structlog

from ...shared.message_bus import MessageBus
from ...shared.schemas import (
    AllocationPlan,
    IdeaVerdict,
    Message,
    MessageType,
)
from ...supervisor.capital_tracker import CapitalTracker

log = structlog.get_logger(__name__)


class AllocationChair:
    agent_id = "room2.allocation_chair"

    def __init__(self) -> None:
        # Set right before every `return None` below so the caller can record
        # *why* an idea that Room 1 approved never actually became a trade —
        # without this, "approved" looks identical to "executed" everywhere
        # downstream, which was the exact gap flagged: reasoning with no link
        # to the real outcome (bought or not, how much, at what target).
        self.last_skip_reason: str = ""

    async def finalise(
        self,
        verdict: IdeaVerdict,
        position_size: dict[str, Any],
        cost_basis: dict[str, Any],
        cartographer: dict[str, Any],
        opportunity_cost: dict[str, Any],
        liquidation: dict[str, Any],
        bull_case: dict[str, Any] | None = None,
        bear_case: dict[str, Any] | None = None,
    ) -> AllocationPlan | None:
        bull_case = bull_case or {}
        bear_case = bear_case or {}
        if not cost_basis.get("viable", False):
            reason = cost_basis.get("reason", "costs_too_high")
            log.warning("allocation_chair.blocked_by_costs", symbol=verdict.symbol, reason=reason)
            self.last_skip_reason = f"Trade costs too high relative to position size ({reason})"
            return None

        oc_verdict = opportunity_cost.get("opportunity_cost_verdict", "deploy")
        if oc_verdict == "wait_for_better_entry":
            condition = opportunity_cost.get("ideal_entry_condition", "")
            log.info("allocation_chair.deferred", symbol=verdict.symbol, condition=condition)
            self.last_skip_reason = f"Waiting for a better entry — {condition}"
            return None

        cart_verdict = cartographer.get("cartographer_verdict", "proceed")
        if cart_verdict == "block":
            log.warning("allocation_chair.blocked_by_cartographer", symbol=verdict.symbol)
            self.last_skip_reason = "Blocked by portfolio diversification/concentration check"
            return None

        quantity       = int(position_size.get("quantity", 0))
        position_value = float(position_size.get("position_value_inr", 0))

        if quantity == 0:
            log.warning("allocation_chair.zero_quantity", symbol=verdict.symbol)
            self.last_skip_reason = "Position sizing came out to zero shares (capital too constrained)"
            return None

        # Reserve capital in ledger
        tracker = CapitalTracker.get()
        try:
            await tracker.reserve_for_lt_desk(
                symbol=verdict.symbol,
                amount=position_value,
            )
        except Exception as e:
            log.error("allocation_chair.capital_reserve_failed", error=str(e))
            self.last_skip_reason = f"Capital reservation failed: {e}"
            return None

        plan = AllocationPlan(
            symbol=verdict.symbol,
            exchange=verdict.exchange,
            direction=verdict.direction,
            quantity=quantity,
            allocated_capital=position_value,
            position_tier=verdict.position_tier,
            trim_candidates=liquidation.get("trim_candidates", []),
            cost_estimate_inr=cost_basis.get("total_cost_inr", 0.0),
            conviction=verdict.final_conviction,
            reasoning=verdict.reasoning,
            target_pct_upside=float(bull_case.get("price_target_pct_upside", 10.0)),
            stop_loss_pct_downside=float(bear_case.get("max_downside_pct", 5.0)),
            time_horizon_weeks=int(bull_case.get("time_horizon_weeks", 8)),
        )

        bus = MessageBus.get()
        await bus.publish(Message(
            type=MessageType.ALLOCATION_PLAN_READY,
            payload=plan.model_dump(),
            source=self.agent_id,
        ))

        log.info("allocation_chair.plan_published",
                 symbol=verdict.symbol,
                 quantity=quantity,
                 value_inr=position_value)
        return plan
