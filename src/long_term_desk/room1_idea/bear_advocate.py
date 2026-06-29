"""BearAdvocate — Constructs the strongest bear case; identifies risks and downside."""
from __future__ import annotations

from typing import Any

import structlog

from ...llm.llm_gateway import LLMGateway
from ...shared.schemas import IdeaQueueItem, LLMTier

log = structlog.get_logger(__name__)

_SYSTEM = """You are the Bear Advocate in an investment committee.
Your role is to make the strongest possible bear case and challenge the bull thesis.
You are NOT trying to be contrarian for its own sake — be rigorous and evidence-based.

Respond ONLY in JSON:
{
  "bear_case": "detailed bear argument (3-5 sentences)",
  "max_downside_pct": 0.0-100.0,
  "key_risks": ["...", "..."],
  "invalidation_scenario": "what would prove the bear case wrong",
  "technical_resistance": "key resistance levels that may cap upside",
  "conviction_score": 0.0-1.0
}
"""


class BearAdvocate:
    agent_id = "room1.bear_advocate"

    async def argue(self, idea: IdeaQueueItem, brief: dict[str, Any],
                    bull_case: dict[str, Any]) -> dict[str, Any]:
        llm = LLMGateway.get()
        user_msg = (
            f"Symbol: {idea.symbol} | Direction: {idea.direction.value}\n"
            f"Bull case summary: {bull_case.get('bull_case', '')}\n"
            f"Bull target: +{bull_case.get('price_target_pct_upside', 0):.1f}% "
            f"over {bull_case.get('time_horizon_weeks', 8)} weeks\n"
            f"Bull catalysts: {bull_case.get('key_catalysts', [])}\n"
            f"Research brief bear points: {brief.get('bear_points', [])}\n"
        )

        result = await llm.complete_json(
            agent_id=self.agent_id,
            system_prompt=_SYSTEM,
            user_prompt=user_msg,
            tier=LLMTier.STANDARD,
        )
        log.info("bear_advocate.argued", symbol=idea.symbol,
                 downside=result.get("max_downside_pct"))
        return result
