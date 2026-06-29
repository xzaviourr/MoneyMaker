"""
MockLLMProvider — returns sensible stub responses so the system can run
in demo mode without any Azure OpenAI credentials.

Used automatically when AZURE_OPENAI_API_KEY is not set.
"""
from __future__ import annotations

import json
import random

from ..shared.schemas import LLMRequest, LLMResponse, LLMTier
from .base_provider import BaseLLMProvider


_MOCK_RESPONSES: dict[str, dict] = {
    # Room 1 agents
    "opportunity_scout": {
        "thesis_summary": "Demo thesis: strong momentum with volume confirmation.",
        "bull_points": ["Strong EMA crossover", "Volume 2x average"],
        "bear_points": ["Near resistance zone"],
        "data_gaps": [],
        "recommended_position_type": "swing",
        "initial_conviction": 0.65,
    },
    "bull_advocate": {
        "bull_case": "Demo bull case: price breaking above 20-day range with institutional backing.",
        "price_target_pct_upside": 8.0,
        "time_horizon_weeks": 6,
        "key_catalysts": ["Sector tailwind", "Strong results expected"],
        "technical_support": "Support at 200 DMA",
        "conviction_score": 0.65,
    },
    "bear_advocate": {
        "bear_case": "Demo bear case: broader market weakness may cap upside.",
        "max_downside_pct": 4.0,
        "key_risks": ["Market correction risk", "Sector rotation away"],
        "invalidation_scenario": "Price holds above 200 DMA on volume",
        "technical_resistance": "Previous swing high",
        "conviction_score": 0.45,
    },
    "devils_advocate": {
        "hidden_assumptions": ["Assumes current volume sustains"],
        "tail_risks": ["Flash crash", "Unexpected earnings miss"],
        "liquidity_concerns": "Adequate daily volume for position size",
        "bull_flaw": "Momentum may be exhausted",
        "bear_flaw": "Ignores strong sector trend",
        "stress_test_score": 0.70,
        "go_no_go_lean": "go",
    },
    "sector_specialist": {
        "sector": "Technology",
        "sector_tailwind": "IT sector outperforming Nifty YTD",
        "competitive_position": "strong",
        "regulatory_risk": "low",
        "peer_comparison": "Top quartile on ROIC vs peers",
        "sector_rotation_signal": "in_favor",
        "specialist_verdict": "support",
        "sector_conviction_modifier": 0.05,
    },
    "momentum_analyst": {
        "trend_quality": "strong",
        "momentum_phase": "mid",
        "relative_strength_vs_nifty": "outperforming",
        "chart_pattern": "Ascending channel with volume support",
        "volume_confirms": True,
        "technical_score": 0.72,
        "momentum_conviction_modifier": 0.08,
        "key_levels": {"support": None, "resistance": None},
    },
    "committee_chair": {
        "verdict": "approve",
        "final_conviction": 0.68,
        "position_tier": "starter",
        "reasoning": "Demo approval: majority of signals aligned, manageable downside.",
        "conditions": [],
        "bull_win_score": 0.65,
        "bear_win_score": 0.40,
    },
    # Room 2
    "portfolio_cartographer": {
        "current_sector_gaps": ["Healthcare underweight"],
        "new_position_sector_fit": "neutral",
        "correlation_risk": "low",
        "portfolio_impact": "neutral",
        "max_position_pct_recommended": 4.0,
        "cartographer_verdict": "proceed",
    },
    "opportunity_cost_analyst": {
        "risk_reward_ratio": 2.0,
        "vs_cash_yield": "better",
        "vs_existing_positions_average": "better",
        "opportunity_cost_verdict": "deploy",
        "ideal_entry_condition": "",
        "confidence_in_timing": 0.65,
    },
    # Guardian
    "news_watchdog": {
        "direction": "neutral",
        "conviction": 0.3,
        "sentiment_extreme": "neutral",
        "rationale": "No significant news detected (demo mode)",
    },
    # Feedback
    "parameter_optimizer": {
        "parameter_name": "",
        "current_value": None,
        "suggested_value": None,
        "expected_improvement": "No suggestion in demo mode",
        "confidence": 0.0,
        "risk_of_degradation": "high",
    },
    "system_review": {
        "summary": "Demo mode: system running on paper broker with mock LLM responses.",
        "working_well": ["Regime classification", "Circuit breaker", "Pod execution"],
        "underperforming": [],
        "pod_actions": [],
        "regime_notes": "Demo mode — no real LLM analysis available",
        "overall_health_score": 0.75,
    },
    # Catalyst hunter / earnings alpha
    "catalyst_hunter": {"has_catalyst": False, "direction": "neutral", "conviction": 0.0},
    "earnings_alpha":  {"direction": "neutral", "conviction": 0.0, "rationale": "Demo mode"},
    "sentiment":       {"direction": "neutral", "conviction": 0.0, "sentiment_extreme": "neutral", "rationale": "Demo mode"},
}


def _mock_for(agent_id: str) -> dict:
    """Find the best mock response for a given agent_id."""
    for key, val in _MOCK_RESPONSES.items():
        if key in agent_id:
            return val
    return {"direction": "neutral", "conviction": 0.0, "result": "demo_mock"}


class MockLLMProvider(BaseLLMProvider):
    """Returns pre-canned JSON responses without calling any API."""

    @property
    def name(self) -> str:
        return "mock"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        mock = _mock_for(request.agent_id)
        content = json.dumps(mock)
        return LLMResponse(
            agent_id=request.agent_id,
            tier=request.tier,
            model_id="mock-model",
            content=content,
            prompt_tokens=50,
            completion_tokens=100,
            cost_usd=0.0,
            latency_ms=10,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Return random unit vectors
        return [[random.gauss(0, 0.1) for _ in range(8)] for _ in texts]

    def supports_tier(self, tier: LLMTier) -> bool:
        return True
