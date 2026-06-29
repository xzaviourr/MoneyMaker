"""DevilsAdvocate — Stress-tests both cases; finds tail risks and hidden assumptions."""
from __future__ import annotations

from typing import Any

import structlog

from ...llm.llm_gateway import LLMGateway
from ...shared.schemas import IdeaQueueItem, LLMTier

log = structlog.get_logger(__name__)

_SYSTEM = """You are the Devil's Advocate in an investment committee.
Your sole purpose is to stress-test BOTH the bull and bear arguments for logical flaws,
hidden assumptions, black-swan risks, and liquidity/execution risks.
Do not conclude "buy" or "sell" — you only raise hard questions and failure modes.

Respond ONLY in JSON:
{
  "hidden_assumptions": ["assumption that could be wrong", "..."],
  "tail_risks": ["low-probability, high-impact risk", "..."],
  "liquidity_concerns": "assessment of position sizing vs. daily volume",
  "bull_flaw": "the weakest link in the bull case",
  "bear_flaw": "the weakest link in the bear case",
  "stress_test_score": 0.0-1.0,
  "go_no_go_lean": "go"|"no_go"|"conditional"
}
"""


class DevilsAdvocate:
    agent_id = "room1.devils_advocate"

    async def stress_test(self, idea: IdeaQueueItem,
                          brief: dict[str, Any],
                          bull_case: dict[str, Any],
                          bear_case: dict[str, Any]) -> dict[str, Any]:
        llm = LLMGateway.get()
        user_msg = (
            f"Symbol: {idea.symbol}\n"
            f"Bull Case: {bull_case.get('bull_case', '')}\n"
            f"  +{bull_case.get('price_target_pct_upside', 0):.1f}% target in "
            f"{bull_case.get('time_horizon_weeks', 8)}w\n"
            f"  Catalysts: {bull_case.get('key_catalysts', [])}\n"
            f"Bear Case: {bear_case.get('bear_case', '')}\n"
            f"  Max downside: -{bear_case.get('max_downside_pct', 0):.1f}%\n"
            f"  Key risks: {bear_case.get('key_risks', [])}\n"
            f"Data gaps: {brief.get('data_gaps', [])}\n"
        )

        result = await llm.complete_json(
            agent_id=self.agent_id,
            system_prompt=_SYSTEM,
            user_prompt=user_msg,
            tier=LLMTier.STANDARD,
        )
        log.info("devils_advocate.result", symbol=idea.symbol,
                 lean=result.get("go_no_go_lean"), stress=result.get("stress_test_score"))
        return result
