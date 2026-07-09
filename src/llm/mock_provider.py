"""
MockLLMProvider — returns sensible stub responses so the system can run
in demo mode without any Azure OpenAI credentials.

Used automatically when AZURE_OPENAI_API_KEY is not set.
"""
from __future__ import annotations

import hashlib
import json
import re
import random

from ..shared.schemas import LLMRequest, LLMResponse, LLMTier
from .base_provider import BaseLLMProvider


def _symbol_from_prompt(prompt: str) -> str:
    """Extract a stock symbol from the user prompt for seeding variation."""
    m = re.search(r'\b([A-Z]{2,10}(?:\.NS|\.BO)?)\b', prompt or "")
    return m.group(1).replace(".NS", "").replace(".BO", "") if m else "STOCK"


def _seed(symbol: str) -> int:
    return int(hashlib.md5(symbol.encode()).hexdigest(), 16) % 10_000


def _v(base: float, seed: int, lo: float = 0.7, hi: float = 1.4) -> float:
    """Scale base by a deterministic factor in [lo, hi] derived from seed."""
    factor = lo + (seed % 1000) / 1000.0 * (hi - lo)
    return round(base * factor, 2)


_BULL_THESES = [
    "Strong breakout above the 52-week consolidation range backed by institutional accumulation.",
    "EMA crossover with 2× average volume — classic trend-continuation setup.",
    "RSI recovering from oversold territory with positive divergence on the weekly.",
    "Price compressing in an ascending triangle — breakout probability is high.",
    "FII buying visible in OI data; sector tailwind from earnings cycle.",
]
_BEAR_THESES = [
    "Macro headwinds and rising yields may compress multiples before the move plays out.",
    "Distribution pattern visible on the monthly — smart money may be exiting.",
    "Nifty at key resistance; a broader correction would drag this stock down first.",
    "Earnings momentum has slowed QoQ — top-line growth story may be peaking.",
    "Sector rotation away from this theme has begun based on FII positioning data.",
]
_CATALYSTS = [
    ["Strong Q3 results expected", "Sector tailwind from PLI scheme", "FII re-entry after correction"],
    ["Management guidance upgrade", "New product launch in Q4", "Export order book expansion"],
    ["Domestic demand recovery", "Margin improvement from lower input costs", "Promoter buying visible"],
    ["Regulatory clearance pipeline", "Capex cycle beneficiary", "Peers' premiumisation trend"],
    ["Index inclusion rumours", "Buyback announcement pending", "Debt reduction ahead of schedule"],
]
_RISKS = [
    ["Market-wide correction risk", "Sector rotation risk if rates stay high"],
    ["Earnings miss on margins", "Global commodity price spike"],
    ["Promoter pledge exposure", "Regulatory tightening in the sector"],
    ["USD/INR headwind for exports", "Competition intensifying from new entrant"],
    ["Q4 demand seasonality weakness", "Valuation premium limits upside"],
]
_SECTORS = ["Technology", "Financials", "Consumer Staples", "Industrials", "Healthcare",
            "Energy", "Materials", "Auto", "FMCG", "Pharma"]
_TREND_QUALITIES = ["strong_uptrend", "moderate_uptrend", "sideways", "weak_uptrend", "recovering"]
_PHASES = ["early", "mid", "late", "exhaustion", "accumulation"]
_LEAN = ["go", "go", "go", "conditional", "no_go"]  # weighted toward go


def _build_responses(symbol: str) -> dict[str, dict]:
    s = _seed(symbol)
    s2, s3, s4, s5 = (s * 3) % 10000, (s * 7) % 10000, (s * 11) % 10000, (s * 13) % 10000

    bull_upside = _v(8.0, s, 0.6, 2.0)
    bear_down   = _v(4.0, s2, 0.5, 1.8)
    bull_conv   = round(min(0.90, _v(0.65, s3, 0.7, 1.25)), 2)
    bear_conv   = round(min(0.85, _v(0.45, s4, 0.7, 1.30)), 2)
    final_conv  = round((bull_conv * 0.6 + (1 - bear_conv) * 0.4), 2)
    approved    = final_conv >= 0.55

    sector      = _SECTORS[s % len(_SECTORS)]
    trend       = _TREND_QUALITIES[s2 % len(_TREND_QUALITIES)]
    phase       = _PHASES[s3 % len(_PHASES)]
    lean        = _LEAN[s4 % len(_LEAN)]
    catalysts   = _CATALYSTS[s % len(_CATALYSTS)]
    risks       = _RISKS[s2 % len(_RISKS)]
    bull_thesis = _BULL_THESES[s3 % len(_BULL_THESES)]
    bear_thesis = _BEAR_THESES[s4 % len(_BEAR_THESES)]
    tech_score  = round(_v(0.72, s5, 0.65, 1.15), 2)

    return {
        "opportunity_scout": {
            "thesis_summary": f"{symbol}: {bull_thesis[:60]}",
            "bull_points": [catalysts[0], catalysts[1]],
            "bear_points": [risks[0], risks[1]],
            "data_gaps": [],
            "recommended_position_type": "swing" if approved else "watchlist",
            "initial_conviction": round(_v(0.65, s, 0.75, 1.15), 2),
        },
        "bull_advocate": {
            "bull_case": f"{symbol}: {bull_thesis}",
            "price_target_pct_upside": bull_upside,
            "time_horizon_weeks": 4 + (s % 8),
            "key_catalysts": catalysts,
            "technical_support": f"Support at {200 - (s % 30)} DMA",
            "conviction_score": bull_conv,
        },
        "bear_advocate": {
            "bear_case": f"{symbol}: {bear_thesis}",
            "max_downside_pct": bear_down,
            "key_risks": risks,
            "invalidation_scenario": f"Price holds above {200 + (s % 20)} DMA on sustained volume",
            "technical_resistance": f"Previous swing high at +{6 + (s % 8)}%",
            "conviction_score": bear_conv,
        },
        "devils_advocate": {
            "hidden_assumptions": [f"Assumes {catalysts[0].lower()} materialises on schedule"],
            "tail_risks": [f"Unexpected {risks[0].lower()}", "Global risk-off event"],
            "liquidity_concerns": "Adequate daily volume for planned position size",
            "bull_flaw": f"Bull case assumes {catalysts[1].lower()} — not yet confirmed",
            "bear_flaw": f"Bear ignores {catalysts[2].lower() if len(catalysts) > 2 else 'sector tailwind'}",
            "stress_test_score": round(_v(0.70, s5, 0.75, 1.10), 2),
            "go_no_go_lean": lean,
        },
        "sector_specialist": {
            "sector": sector,
            "sector_tailwind": f"{sector} outperforming Nifty YTD by {4 + (s % 12)}%",
            "competitive_position": "strong" if s % 3 == 0 else "moderate",
            "regulatory_risk": "low" if s % 4 != 0 else "medium",
            "peer_comparison": f"Top {25 + (s % 30)}th percentile on ROIC vs {sector} peers",
            "sector_rotation_signal": "in_favor" if s % 3 != 2 else "neutral",
            "specialist_verdict": "support" if approved else "neutral",
            "sector_conviction_modifier": round(_v(0.05, s2, 0.5, 2.0), 3),
        },
        "momentum_analyst": {
            "trend_quality": trend,
            "momentum_phase": phase,
            "relative_strength_vs_nifty": "outperforming" if s % 3 != 2 else "inline",
            "chart_pattern": f"{'Ascending' if approved else 'Descending'} channel — {phase} phase",
            "volume_confirms": s % 5 != 4,
            "technical_score": min(0.95, tech_score),
            "momentum_conviction_modifier": round(_v(0.08, s3, 0.5, 1.8), 3),
            "key_levels": {"support": None, "resistance": None},
        },
        "committee_chair": {
            "verdict": "approve" if approved else "reject",
            "final_conviction": final_conv,
            "position_tier": "core" if final_conv > 0.72 else ("starter" if final_conv > 0.58 else "skip"),
            "reasoning": (
                f"{symbol}: Bull +{bull_upside}% vs Bear -{bear_down}% — "
                f"{'risk/reward favours entry' if approved else 'risk/reward insufficient'}. "
                f"{sector} sector {'supportive' if s % 3 != 2 else 'neutral'}. "
                f"Momentum {trend.replace('_', ' ')} ({phase} phase). "
                f"Devil's lean: {lean.replace('_', ' ')}."
            ),
            "conditions": [],
            "bull_win_score": bull_conv,
            "bear_win_score": bear_conv,
            "approved": approved,
        },
    }
_STATIC_RESPONSES: dict[str, dict] = {
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


_ROOM1_KEYS = {"opportunity_scout", "bull_advocate", "bear_advocate",
               "devils_advocate", "sector_specialist", "momentum_analyst", "committee_chair"}


def _mock_for(agent_id: str, user_prompt: str = "") -> dict:
    symbol = _symbol_from_prompt(user_prompt)
    # Room 1 agents get symbol-varied responses
    for key in _ROOM1_KEYS:
        if key in agent_id:
            return _build_responses(symbol)[key]
    # Everything else is static
    for key, val in _STATIC_RESPONSES.items():
        if key in agent_id:
            return val
    return {"direction": "neutral", "conviction": 0.0, "result": "demo_mock"}


class MockLLMProvider(BaseLLMProvider):
    """Returns pre-canned JSON responses without calling any API."""

    @property
    def name(self) -> str:
        return "mock"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        mock = _mock_for(request.agent_id, request.user_prompt or "")
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
