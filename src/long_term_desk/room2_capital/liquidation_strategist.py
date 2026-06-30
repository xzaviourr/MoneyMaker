"""LiquidationStrategist — Identifies which current positions to trim to fund the new trade."""
from __future__ import annotations

from typing import Any

import structlog

from ...brokers.broker_gateway import BrokerGateway
from ...llm.llm_gateway import LLMGateway
from ...shared.schemas import IdeaVerdict, LLMTier

log = structlog.get_logger(__name__)

_SYSTEM = """You are the Liquidation Strategist in a capital allocation committee.
Given a need to free up capital for a new position, identify the best candidates to trim or exit.
Prefer candidates that: have achieved their target, are showing deteriorating thesis, or
are highly correlated with the new position.

Respond ONLY in JSON:
{
  "trim_candidates": [
    {
      "symbol": "...",
      "action": "full_exit"|"trim_50pct"|"trim_25pct",
      "reason": "brief reason",
      "urgency": "immediate"|"gradual"
    }
  ],
  "capital_freed_estimate_pct": 0.0-100.0,
  "liquidation_rationale": "summary of why these candidates were chosen"
}
"""


class LiquidationStrategist:
    agent_id = "room2.liquidation_strategist"

    async def identify_trims(self, verdict: IdeaVerdict,
                              capital_needed_pct: float,
                              cartographer: dict[str, Any]) -> dict[str, Any]:
        broker = BrokerGateway.get()
        llm    = LLMGateway.get()

        try:
            positions = await broker.get_positions()
            pos_detail = [
                {
                    "symbol":        p.symbol,
                    "unrealised_pnl": p.unrealized_pnl_pct,
                    "days_held":     (p.days_held if hasattr(p, "days_held") else "unknown"),
                    "pnl_inr":       p.unrealized_pnl,
                }
                for p in positions[:20]
            ]
        except Exception:
            pos_detail = []

        user_msg = (
            f"New trade: {verdict.symbol} ({verdict.direction.value})\n"
            f"Capital needed: ~{capital_needed_pct:.1f}% of pillar\n"
            f"Portfolio impact: {cartographer.get('portfolio_impact', '')}\n"
            f"Correlation risk: {cartographer.get('correlation_risk', '')}\n"
            f"Existing positions:\n{pos_detail}\n"
        )

        result = await llm.complete_json(
            agent_id=self.agent_id,
            system_prompt=_SYSTEM,
            user_prompt=user_msg,
            tier=LLMTier.FAST,
        )
        log.info("liquidation_strategist.done", symbol=verdict.symbol,
                 trims=len(result.get("trim_candidates", [])))
        return result
