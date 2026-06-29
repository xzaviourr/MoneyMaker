"""OpportunityScout — First agent in Room 1; structures raw IdeaQueueItem into a ResearchBrief."""
from __future__ import annotations

from typing import Any

import structlog

from ...llm.llm_gateway import LLMGateway
from ...shared.schemas import AgentVote, IdeaQueueItem, LLMTier

log = structlog.get_logger(__name__)

_SYSTEM = """You are OpportunityScout, the first analyst in a long-term equity research committee.
Your job is to structure an investment thesis from raw strategy signals and produce a
ResearchBrief that the committee will debate.

Respond ONLY in JSON:
{
  "thesis_summary": "2-3 sentence investment thesis",
  "bull_points": ["...", "..."],
  "bear_points": ["...", "..."],
  "data_gaps": ["missing info that should be researched"],
  "recommended_position_type": "swing"|"core"|"options",
  "initial_conviction": 0.0-1.0
}
"""


class OpportunityScout:
    agent_id = "room1.opportunity_scout"

    async def brief(self, idea: IdeaQueueItem) -> dict[str, Any]:
        llm = LLMGateway.get()
        strat_summary = ", ".join(idea.supporting_strategies)
        contra_summary = ", ".join(idea.contradicting_strategies) if idea.contradicting_strategies else "none"

        user_msg = (
            f"Symbol: {idea.symbol} ({idea.exchange.value})\n"
            f"Direction: {idea.direction.value}\n"
            f"Conviction score: {idea.conviction_score:.2f}\n"
            f"Supporting strategies: {strat_summary}\n"
            f"Contradicting strategies: {contra_summary}\n"
            f"Number of confirming signals: {len(idea.supporting_strategies)}\n"
        )

        if idea.signals:
            for s in idea.signals[:3]:  # top 3 signals for context
                user_msg += (
                    f"\n--- {s.strategy_name} ---\n"
                    f"Rationale: {s.rationale}\n"
                    f"Indicators: {s.supporting_indicators}\n"
                )

        result = await llm.complete_json(
            agent_id=self.agent_id,
            system_prompt=_SYSTEM,
            user_prompt=user_msg,
            tier=LLMTier.STANDARD,
        )
        log.info("opportunity_scout.brief", symbol=idea.symbol,
                 thesis=result.get("thesis_summary", "")[:80])
        return result
