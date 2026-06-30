"""
SystemReviewAgent — weekly LLM-powered self-review.

Every Sunday (or on demand), runs a comprehensive review:
  - All strategy performance stats
  - Agent calibration scores
  - Regime fit analysis
  - Suggestions for pod lifecycle changes (promote/demote/kill)
  - Publishes a SystemHealthReport
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import structlog

from ..llm.llm_gateway import LLMGateway
from ..shared.message_bus import MessageBus
from ..shared.schemas import LLMTier, Message, MessageType, SystemHealthReport
from .agent_calibration_engine import AgentCalibrationEngine
from .regime_adjusted_scorer import RegimeAdjustedScorer
from .strategy_performance_analyzer import StrategyPerformanceAnalyzer

log = structlog.get_logger(__name__)

_SYSTEM = """You are the System Review Agent for MoneyMaker, an algorithmic trading system.
You have been given a weekly performance summary. Your job is to:
1. Identify what is working well
2. Identify what is underperforming and should be changed
3. Suggest specific pod lifecycle actions (promote, demote, review, kill)
4. Recommend regime-specific adjustments

Be precise, data-driven, and actionable. Format as JSON:
{
  "summary": "2-3 sentence executive summary",
  "working_well": ["...", "..."],
  "underperforming": ["...", "..."],
  "pod_actions": [
    {"pod": "pod_name", "action": "promote"|"demote"|"review"|"kill", "reason": "..."}
  ],
  "regime_notes": "recommendations for regime-specific adjustments",
  "overall_health_score": 0.0-1.0
}
"""


class SystemReviewAgent:
    def __init__(
        self,
        analyzer: StrategyPerformanceAnalyzer,
        calibration: AgentCalibrationEngine,
        scorer: RegimeAdjustedScorer,
    ) -> None:
        self._analyzer   = analyzer
        self._calibration = calibration
        self._scorer     = scorer
        self._bus        = MessageBus.get()
        self._running    = False

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._weekly_loop())
        log.info("system_review_agent.started")

    async def stop(self) -> None:
        self._running = False

    async def _weekly_loop(self) -> None:
        while self._running:
            await asyncio.sleep(7 * 24 * 3600)  # weekly
            try:
                await self.run_review()
            except Exception:
                log.exception("system_review.loop_error")

    async def run_review(self) -> dict[str, Any]:
        log.info("system_review.starting")

        all_stats   = self._analyzer.all_stats()
        agent_weights = {
            k: v.model_dump()
            for k, v in self._calibration.get_weights().items()
        }
        weak = self._analyzer.weakest_strategies(n=5)

        context = (
            f"Date: {datetime.utcnow().strftime('%Y-%m-%d')}\n"
            f"Strategy stats ({len(all_stats)} strategies):\n"
            f"{all_stats[:10]}\n\n"
            f"Agent calibration weights:\n{agent_weights}\n\n"
            f"Weakest strategies: {weak}\n"
        )

        try:
            llm    = LLMGateway.get()
            result = await llm.complete_json(
                agent_id="feedback.system_review",
                system_prompt=_SYSTEM,
                user_prompt=context,
                tier=LLMTier.DEEP,
            )
        except Exception:
            log.exception("system_review.llm_error")
            return {}

        health_score = float(result.get("overall_health_score", 0.5))
        report = SystemHealthReport(
            overall_health_score=health_score,
            summary=result.get("summary", ""),
            working_well=result.get("working_well", []),
            underperforming=result.get("underperforming", []),
            pod_actions=result.get("pod_actions", []),
            regime_notes=result.get("regime_notes", ""),
            generated_at=datetime.utcnow(),
        )

        await self._bus.publish(Message(
            type=MessageType.SYSTEM_HEALTH_REPORT,
            payload=report.model_dump(),
            source="system_review_agent",
        ))

        log.info("system_review.done",
                 health=health_score,
                 actions=len(result.get("pod_actions", [])))
        return result
