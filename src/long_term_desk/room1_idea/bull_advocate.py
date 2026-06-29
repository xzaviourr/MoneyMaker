"""BullAdvocate — Constructs the strongest possible bull case; scores risk/reward upside."""
from __future__ import annotations

from typing import Any

import structlog

from ...llm.llm_gateway import LLMGateway
from ...shared.schemas import IdeaQueueItem, LLMTier

log = structlog.get_logger(__name__)

_SYSTEM = """You are the Bull Advocate in an investment committee.
Your role is to make the strongest possible bull case for the trade.
Do NOT introduce strawman bull arguments — be rigorous, evidence-based, and specific.

Respond ONLY in JSON:
{
  "bull_case": "detailed bull argument (3-5 sentences)",
  "price_target_pct_upside": 0.0-200.0,
  "time_horizon_weeks": 4-52,
  "key_catalysts": ["...", "..."],
  "technical_support": "key technical levels supporting the long",
  "conviction_score": 0.0-1.0
}
"""


class BullAdvocate:
    agent_id = "room1.bull_advocate"

    async def argue(self, idea: IdeaQueueItem, brief: dict[str, Any]) -> dict[str, Any]:
        llm = LLMGateway.get()
        user_msg = (
            f"Symbol: {idea.symbol} | Direction: {idea.direction.value}\n"
            f"Research Brief:\n"
            f"  Thesis: {brief.get('thesis_summary', '')}\n"
            f"  Bull points: {brief.get('bull_points', [])}\n"
            f"  Bear points: {brief.get('bear_points', [])}\n"
            f"  Conviction: {brief.get('initial_conviction', 0):.2f}\n"
            f"  Position type: {brief.get('recommended_position_type', 'swing')}\n"
        )

        result = await llm.complete_json(
            agent_id=self.agent_id,
            system_prompt=_SYSTEM,
            user_prompt=user_msg,
            tier=LLMTier.STANDARD,
        )
        log.info("bull_advocate.argued", symbol=idea.symbol,
                 upside=result.get("price_target_pct_upside"))
        return result
