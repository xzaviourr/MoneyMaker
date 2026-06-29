"""MomentumAnalyst — Technical momentum quality: trend strength, RS rank, chart structure."""
from __future__ import annotations

from typing import Any

import structlog

from ...llm.llm_gateway import LLMGateway
from ...shared.schemas import IdeaQueueItem, LLMTier

log = structlog.get_logger(__name__)

_SYSTEM = """You are the Momentum Analyst in an investment committee.
You assess the technical quality of a trade idea: trend health, price momentum,
relative strength vs Nifty50, chart pattern quality, and volume profile.
Provide a concise technical verdict and conviction modifier.

Respond ONLY in JSON:
{
  "trend_quality": "strong"|"moderate"|"weak"|"broken",
  "momentum_phase": "early"|"mid"|"late"|"reversal",
  "relative_strength_vs_nifty": "outperforming"|"inline"|"underperforming",
  "chart_pattern": "description of current pattern",
  "volume_confirms": true|false,
  "technical_score": 0.0-1.0,
  "momentum_conviction_modifier": -0.2 to 0.2,
  "key_levels": {"support": null, "resistance": null}
}
"""


class MomentumAnalyst:
    agent_id = "room1.momentum_analyst"

    async def assess(self, idea: IdeaQueueItem, brief: dict[str, Any],
                     bull_case: dict[str, Any]) -> dict[str, Any]:
        llm = LLMGateway.get()
        # Provide raw indicator data from signals if available
        indicator_data = {}
        if idea.signals:
            for s in idea.signals[:5]:
                indicator_data[s.strategy_name] = s.supporting_indicators

        user_msg = (
            f"Symbol: {idea.symbol}\n"
            f"Direction: {idea.direction.value}\n"
            f"Technical indicators from strategies: {indicator_data}\n"
            f"Bull technical support: {bull_case.get('technical_support', '')}\n"
            f"Key catalysts: {bull_case.get('key_catalysts', [])}\n"
        )

        result = await llm.complete_json(
            agent_id=self.agent_id,
            system_prompt=_SYSTEM,
            user_prompt=user_msg,
            tier=LLMTier.FAST,
        )
        log.info("momentum_analyst.assessed", symbol=idea.symbol,
                 trend=result.get("trend_quality"),
                 score=result.get("technical_score"))
        return result
