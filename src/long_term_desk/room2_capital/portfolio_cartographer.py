"""PortfolioCartographer — Maps current portfolio: sector exposures, factor tilts, concentration."""
from __future__ import annotations

from typing import Any

import structlog

from ...brokers.broker_gateway import BrokerGateway
from ...llm.llm_gateway import LLMGateway
from ...shared.schemas import IdeaVerdict, LLMTier

log = structlog.get_logger(__name__)

_SYSTEM = """You are the Portfolio Cartographer in a capital allocation committee.
Given the current portfolio positions and proposed new trade, assess:
1. How this new position changes sector/factor exposure
2. Whether it increases problematic concentration
3. How correlated it is with existing holdings

Respond ONLY in JSON:
{
  "current_sector_gaps": ["sectors underweight", "..."],
  "new_position_sector_fit": "fills_gap"|"neutral"|"increases_concentration",
  "correlation_risk": "high"|"medium"|"low",
  "portfolio_impact": "improves_diversification"|"neutral"|"degrades_diversification",
  "max_position_pct_recommended": 1.0-10.0,
  "cartographer_verdict": "proceed"|"caution"|"block"
}
"""


class PortfolioCartographer:
    agent_id = "room2.portfolio_cartographer"

    async def map(self, verdict: IdeaVerdict) -> dict[str, Any]:
        broker = BrokerGateway.get()
        llm    = LLMGateway.get()

        try:
            positions = await broker.get_positions()
            pos_summary = [
                {
                    "symbol":   p.symbol,
                    "side":     p.side,
                    "value":    p.quantity * p.current_price if p.current_price else 0,
                    "pnl_pct":  p.unrealized_pnl_pct,
                }
                for p in positions[:20]
            ]
        except Exception:
            pos_summary = []

        user_msg = (
            f"Proposed trade: {verdict.symbol} ({verdict.direction.value})\n"
            f"Conviction: {verdict.final_conviction:.2f}\n"
            f"Position tier: {verdict.position_tier}\n"
            f"Current portfolio ({len(pos_summary)} positions): {pos_summary}\n"
        )

        result = await llm.complete_json(
            agent_id=self.agent_id,
            system_prompt=_SYSTEM,
            user_prompt=user_msg,
            tier=LLMTier.FAST,
        )
        log.info("portfolio_cartographer.mapped", symbol=verdict.symbol,
                 portfolio_impact=result.get("portfolio_impact"),
                 verdict=result.get("cartographer_verdict"))
        return result
