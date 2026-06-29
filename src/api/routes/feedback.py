"""Feedback routes — strategy stats, agent weights, system health."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter()

_feedback_system: Any = None


def set_feedback_system(system: Any) -> None:
    global _feedback_system
    _feedback_system = system


@router.get("/summary")
async def get_feedback_summary() -> dict:
    if not _feedback_system:
        return {"status": "feedback system not initialised"}
    try:
        analyzer = _feedback_system.analyzer
        calibration = _feedback_system.calibration
        return {
            "strategies":     analyzer.all_stats(),
            "agent_weights":  {k: v.model_dump()
                               for k, v in calibration.get_weights().items()},
            "weakest":        analyzer.weakest_strategies(5),
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/strategies/{strategy}")
async def get_strategy_stats(strategy: str) -> dict:
    if not _feedback_system:
        return {}
    return _feedback_system.analyzer.get_stats(strategy)


@router.post("/review")
async def trigger_review() -> dict:
    if not _feedback_system:
        return {"error": "not initialised"}
    result = await _feedback_system.review_agent.run_review()
    return result
