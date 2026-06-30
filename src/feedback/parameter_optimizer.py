"""
ParameterOptimizer — suggests parameter updates for pods/strategies based on rolling performance.

Uses a simple hill-climbing approach:
  - If win_rate and Sharpe both declining → flag for parameter review
  - Asks LLM (REASONING tier) for parameter suggestion with context
  - Publishes ParameterUpdate event (never applies directly — human or AutoApprover gates it)
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog

from ..llm.llm_gateway import LLMGateway
from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    LLMTier,
    Message,
    MessageType,
    ParameterUpdate,
)
from .strategy_performance_analyzer import StrategyPerformanceAnalyzer

log = structlog.get_logger(__name__)

_SYSTEM = """You are a quantitative strategy optimizer.
Given a strategy's current performance statistics and parameter set, suggest ONE parameter
change that is most likely to improve future performance. Be conservative and evidence-based.
Do NOT suggest changes that would overfit to recent data.

Respond ONLY in JSON:
{
  "parameter_name": "name of parameter to change",
  "current_value": <current value>,
  "suggested_value": <new value>,
  "expected_improvement": "brief expectation",
  "confidence": 0.0-1.0,
  "risk_of_degradation": "low"|"medium"|"high"
}
"""


class ParameterOptimizer:
    def __init__(self, analyzer: StrategyPerformanceAnalyzer) -> None:
        self._analyzer = analyzer
        self._bus      = MessageBus.get()

    async def run_optimization_pass(self, strategy: str, current_params: dict[str, Any]) -> None:
        stats = self._analyzer.get_stats(strategy)
        if not stats or stats.get("total", 0) < 20:
            return  # not enough data

        win_rate = stats.get("win_rate", 0)
        sharpe   = stats.get("sharpe", 0)

        # Only suggest changes if performance is deteriorating
        if win_rate >= 0.50 and sharpe >= 0.5:
            return

        try:
            llm    = LLMGateway.get()
            result = await llm.complete_json(
                agent_id="feedback.parameter_optimizer",
                system_prompt=_SYSTEM,
                user_prompt=(
                    f"Strategy: {strategy}\n"
                    f"Current params: {current_params}\n"
                    f"Performance: win_rate={win_rate:.2f}, sharpe={sharpe:.2f}, "
                    f"expectancy={stats.get('expectancy', 0):.2f}, "
                    f"total_trades={stats.get('total', 0)}\n"
                ),
                tier=LLMTier.REASONING,
            )
        except Exception:
            log.exception("parameter_optimizer.llm_error", strategy=strategy)
            return

        confidence = float(result.get("confidence", 0.0))
        risk       = result.get("risk_of_degradation", "high")
        param_name = result.get("parameter_name", "")

        if not param_name or confidence < 0.65 or risk == "high":
            return

        update = ParameterUpdate(
            pod_id=strategy,
            strategy=strategy,
            parameter_name=param_name,
            old_value=result.get("current_value"),
            new_value=result.get("suggested_value"),
            reason=result.get("expected_improvement", ""),
            confidence=confidence,
            regime_context=None,
        )

        await self._bus.publish(Message(
            type=MessageType.PARAMETERS_UPDATED,
            payload=update.model_dump(),
            source="parameter_optimizer",
        ))

        log.info("parameter_optimizer.suggestion",
                 strategy=strategy, param=param_name,
                 old=result.get("current_value"), new=result.get("suggested_value"))
