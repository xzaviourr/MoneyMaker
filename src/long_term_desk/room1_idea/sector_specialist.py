"""SectorSpecialist — Provides sector-specific context: rotation, competitive position, regulation."""
from __future__ import annotations

from typing import Any

import structlog

from ...llm.llm_gateway import LLMGateway
from ...shared.schemas import IdeaQueueItem, LLMTier

log = structlog.get_logger(__name__)

_SYSTEM = """You are the Sector Specialist in an investment committee.
You provide deep sector context: industry trends, competitive moat, regulatory risk,
sector rotation signals, and how this name compares to sector peers.
Focus specifically on Indian market dynamics (BSE/NSE sectors, SEBI regulations, RBI policy).

Respond ONLY in JSON:
{
  "sector": "sector name",
  "sector_tailwind": "current macro/regulatory tailwind or headwind",
  "competitive_position": "strong"|"average"|"weak",
  "regulatory_risk": "high"|"medium"|"low",
  "peer_comparison": "how this stock ranks vs sector peers",
  "sector_rotation_signal": "in_favor"|"neutral"|"out_of_favor",
  "specialist_verdict": "support"|"neutral"|"oppose",
  "sector_conviction_modifier": -0.2 to 0.2
}
"""


class SectorSpecialist:
    agent_id = "room1.sector_specialist"

    async def assess(self, idea: IdeaQueueItem, brief: dict[str, Any]) -> dict[str, Any]:
        llm = LLMGateway.get()
        user_msg = (
            f"Symbol: {idea.symbol} ({idea.exchange.value})\n"
            f"Thesis: {brief.get('thesis_summary', '')}\n"
            f"Bull points: {brief.get('bull_points', [])}\n"
            f"Position type: {brief.get('recommended_position_type', 'swing')}\n"
        )

        result = await llm.complete_json(
            agent_id=self.agent_id,
            system_prompt=_SYSTEM,
            user_prompt=user_msg,
            tier=LLMTier.STANDARD,
        )
        log.info("sector_specialist.assessed", symbol=idea.symbol,
                 verdict=result.get("specialist_verdict"),
                 sector=result.get("sector"))
        return result
