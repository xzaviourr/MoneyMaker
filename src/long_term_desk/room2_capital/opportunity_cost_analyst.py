"""OpportunityCostAnalyst — Compares new trade to alternatives; ensures capital is deployed optimally."""
from __future__ import annotations

from typing import Any

import structlog

from ...llm.llm_gateway import LLMGateway
from ...shared.schemas import IdeaVerdict, LLMTier

log = structlog.get_logger(__name__)

_SYSTEM = """You are the Opportunity Cost Analyst in a capital allocation committee.
This trade has ALREADY cleared a full debate — Bull, Bear, and Devil's Advocate
have all scrutinized the idea itself. Your only job here is entry TIMING, not
re-judging whether the idea is good.

"wait_for_better_entry" was being over-used as a default answer to ordinary
uncertainty, which meant almost nothing ever actually got bought. Reserve it
for a SPECIFIC, near-term, nameable reason — e.g. earnings due within days,
price technically extended right at a resistance level with no confirmation
yet, a known event about to move the stock. "I'm not fully sure" or "could
pull back" are not specific reasons — every trade has some uncertainty, and
that's already been weighed by the debate that approved this idea.

Default to "deploy" unless you can name the specific condition that makes
right now clearly worse than a near-term alternative entry point.

Respond ONLY in JSON:
{
  "risk_reward_ratio": 0.0-10.0,
  "vs_cash_yield": "better"|"similar"|"worse",
  "vs_existing_positions_average": "better"|"similar"|"worse",
  "opportunity_cost_verdict": "deploy"|"hold"|"wait_for_better_entry",
  "ideal_entry_condition": "description of better entry if wait recommended",
  "confidence_in_timing": 0.0-1.0
}
"""


class OpportunityCostAnalyst:
    agent_id = "room2.opportunity_cost_analyst"

    async def assess(self, verdict: IdeaVerdict,
                     bull_case: dict[str, Any],
                     bear_case: dict[str, Any]) -> dict[str, Any]:
        llm = LLMGateway.get()

        upside   = bull_case.get("price_target_pct_upside", 10.0)
        downside = bear_case.get("max_downside_pct", 5.0)
        horizon  = bull_case.get("time_horizon_weeks", 8)
        rr_ratio = upside / downside if downside > 0 else upside

        user_msg = (
            f"Trade: {verdict.symbol} ({verdict.direction.value})\n"
            f"Conviction: {verdict.final_conviction:.2f}\n"
            f"Upside: +{upside:.1f}%, Downside: -{downside:.1f}% over ~{horizon}w\n"
            f"Computed R:R ratio: {rr_ratio:.2f}\n"
            f"Position tier: {verdict.position_tier}\n"
            f"Conditions: {verdict.conditions}\n"
        )

        result = await llm.complete_json(
            agent_id=self.agent_id,
            system_prompt=_SYSTEM,
            user_prompt=user_msg,
            tier=LLMTier.FAST,
        )
        log.info("opportunity_cost_analyst.done", symbol=verdict.symbol,
                 rr=rr_ratio, oc_verdict=result.get("opportunity_cost_verdict"))
        return result
