"""CommitteeChair — Synthesises Room 1 debate and issues a final IdeaVerdict with vote weights."""
from __future__ import annotations

from typing import Any

import structlog

from ...audit.explainability_ledger import ExplainabilityLedger
from ...llm.llm_gateway import LLMGateway
from ...shared.schemas import (
    AgentVote,
    IdeaQueueItem,
    IdeaVerdict,
    LLMTier,
    SignalDirection,
)

log = structlog.get_logger(__name__)

_SYSTEM = """You are the Committee Chair of an equity investment committee.
You have heard from: OpportunityScout, BullAdvocate, BearAdvocate, DevilsAdvocate,
SectorSpecialist, and MomentumAnalyst.

Your job is to weigh all arguments, reach a final verdict, and determine:
1. Whether to pass the idea to the Capital Allocation room (Room 2)
2. The final conviction score (0-1)
3. Suggested position sizing tier

Respond ONLY in JSON:
{
  "verdict": "approve"|"reject"|"conditional",
  "final_conviction": 0.0-1.0,
  "position_tier": "full"|"half"|"starter",
  "reasoning": "3-4 sentence synthesis of why you reached this verdict",
  "conditions": ["condition if conditional, else empty"],
  "bull_win_score": 0.0-1.0,
  "bear_win_score": 0.0-1.0
}
"""


class CommitteeChair:
    agent_id = "room1.committee_chair"

    async def deliberate(
        self,
        idea: IdeaQueueItem,
        brief: dict[str, Any],
        bull_case: dict[str, Any],
        bear_case: dict[str, Any],
        devils_case: dict[str, Any],
        sector: dict[str, Any],
        momentum: dict[str, Any],
    ) -> IdeaVerdict:
        llm = LLMGateway.get()
        user_msg = (
            f"Symbol: {idea.symbol} | Direction: {idea.direction.value}\n\n"
            f"SCOUT:\n  Thesis: {brief.get('thesis_summary', '')}\n"
            f"  Initial conviction: {brief.get('initial_conviction', 0):.2f}\n\n"
            f"BULL ({bull_case.get('conviction_score', 0):.2f}): {bull_case.get('bull_case', '')[:300]}\n"
            f"  Target: +{bull_case.get('price_target_pct_upside', 0):.1f}% "
            f"in {bull_case.get('time_horizon_weeks', 8)}w\n\n"
            f"BEAR ({bear_case.get('conviction_score', 0):.2f}): {bear_case.get('bear_case', '')[:300]}\n"
            f"  Max drawdown: -{bear_case.get('max_downside_pct', 0):.1f}%\n\n"
            f"DEVIL: lean={devils_case.get('go_no_go_lean', 'conditional')}, "
            f"stress={devils_case.get('stress_test_score', 0):.2f}\n"
            f"  Tail risks: {devils_case.get('tail_risks', [])}\n\n"
            f"SECTOR: {sector.get('sector', '')}, rotation={sector.get('sector_rotation_signal', '')}, "
            f"verdict={sector.get('specialist_verdict', '')}, "
            f"modifier={sector.get('sector_conviction_modifier', 0):+.2f}\n\n"
            f"MOMENTUM: trend={momentum.get('trend_quality', '')}, "
            f"phase={momentum.get('momentum_phase', '')}, "
            f"modifier={momentum.get('momentum_conviction_modifier', 0):+.2f}\n"
        )

        result = await llm.complete_json(
            agent_id=self.agent_id,
            system_prompt=_SYSTEM,
            user_prompt=user_msg,
            tier=LLMTier.REASONING,
        )

        verdict_str  = result.get("verdict", "reject")
        final_conv   = float(result.get("final_conviction", 0.0))
        position_tier = result.get("position_tier", "starter")
        reasoning    = result.get("reasoning", "")
        conditions   = result.get("conditions", [])

        log.info("committee_chair.verdict", symbol=idea.symbol,
                 verdict=verdict_str, conviction=final_conv)

        # "conditional" is the committee's normal cautious verdict, not a rejection —
        # Room 2 already sizes it down via position_tier/final_conviction, so let it
        # through as a smaller, monitored position instead of discarding the idea.
        approved = verdict_str in ("approve", "conditional")

        await ExplainabilityLedger.get().record(
            agent_id=self.agent_id,
            decision=verdict_str,
            reasoning=reasoning,
            symbol=idea.symbol,
            inputs={"bull_conviction": bull_case.get("conviction_score"),
                    "bear_conviction": bear_case.get("conviction_score")},
            outputs={"final_conviction": final_conv, "position_tier": position_tier,
                     "approved": approved},
        )

        votes = [
            AgentVote(
                agent_id="bull_advocate",
                verdict="approve" if bull_case.get("conviction_score", 0.5) >= 0.5 else "reject",
                confidence=float(bull_case.get("conviction_score", 0.5)),
                reasoning=bull_case.get("bull_case", "")[:120],
                weight=1.0,
            ),
            AgentVote(
                agent_id="bear_advocate",
                verdict="reject",
                confidence=float(bear_case.get("conviction_score", 0.5)),
                reasoning=bear_case.get("bear_case", "")[:120],
                weight=1.0,
            ),
            AgentVote(
                agent_id="sector_specialist",
                verdict=(
                    "approve" if sector.get("specialist_verdict") == "support"
                    else ("reject" if sector.get("specialist_verdict") == "oppose" else "abstain")
                ),
                confidence=0.6,
                reasoning=sector.get("peer_comparison", "")[:80],
                weight=0.8,
            ),
            AgentVote(
                agent_id="momentum_analyst",
                verdict="approve" if float(momentum.get("momentum_conviction_modifier", 0)) >= 0 else "reject",
                confidence=float(momentum.get("technical_score", 0.5)),
                reasoning=momentum.get("chart_pattern", "")[:80],
                weight=0.7,
            ),
        ]

        return IdeaVerdict(
            symbol=idea.symbol,
            exchange=idea.exchange,
            direction=idea.direction,
            approved=approved,
            trade_approved=approved,
            final_conviction=final_conv,
            confidence_score=final_conv,
            position_tier=position_tier,
            reasoning=reasoning,
            reasoning_summary=reasoning,
            votes=votes,
            conditions=conditions,
        )
