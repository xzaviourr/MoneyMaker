"""
Model-tier definitions and agent → tier routing table.
Agents never specify a model; they declare a tier and the gateway resolves it.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..shared.schemas import LLMTier


@dataclass(frozen=True)
class LLMTierConfig:
    tier: LLMTier
    deployment_key: str   # key into config.toml [llm.azure_openai.deployments]
    max_tokens: int
    temperature: float
    target_latency_ms: int
    cost_per_1k_input: float   # USD
    cost_per_1k_output: float  # USD


TIER_CONFIGS: dict[LLMTier, LLMTierConfig] = {
    LLMTier.FAST: LLMTierConfig(
        tier=LLMTier.FAST,
        deployment_key="fast",
        max_tokens=1024,
        temperature=0.1,
        target_latency_ms=500,
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.0006,
    ),
    LLMTier.STANDARD: LLMTierConfig(
        tier=LLMTier.STANDARD,
        deployment_key="standard",
        max_tokens=4096,
        temperature=0.2,
        target_latency_ms=3000,
        cost_per_1k_input=0.005,
        cost_per_1k_output=0.015,
    ),
    LLMTier.REASONING: LLMTierConfig(
        tier=LLMTier.REASONING,
        deployment_key="reasoning",
        max_tokens=8192,
        temperature=1.0,   # o1-mini requires temperature=1
        target_latency_ms=8000,
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.012,
    ),
    LLMTier.DEEP: LLMTierConfig(
        tier=LLMTier.DEEP,
        deployment_key="deep",
        max_tokens=32768,
        temperature=1.0,
        target_latency_ms=30000,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.060,
    ),
    LLMTier.EMBEDDING: LLMTierConfig(
        tier=LLMTier.EMBEDDING,
        deployment_key="embedding",
        max_tokens=8191,
        temperature=0.0,
        target_latency_ms=200,
        cost_per_1k_input=0.0001,
        cost_per_1k_output=0.0,
    ),
}

# ── Agent → Tier mapping (from architecture §9) ────────────────────────────

AGENT_TIER_MAP: dict[str, LLMTier] = {
    # Intraday pods
    "pod.strategy_agent":          LLMTier.FAST,
    "pod.risk_agent":              LLMTier.FAST,
    # Guardian
    "guardian.position_monitor":   LLMTier.FAST,
    "guardian.news_watchdog":      LLMTier.STANDARD,
    "guardian.portfolio_guardian": LLMTier.STANDARD,
    "guardian.macro_shift":        LLMTier.STANDARD,
    "guardian.correlation":        LLMTier.FAST,
    "guardian.earnings_calendar":  LLMTier.FAST,
    # Room 1
    "room1.opportunity_scout":     LLMTier.STANDARD,
    "room1.bull_advocate":         LLMTier.STANDARD,
    "room1.bear_advocate":         LLMTier.STANDARD,
    "room1.devils_advocate":       LLMTier.STANDARD,
    "room1.sector_specialist":     LLMTier.STANDARD,
    "room1.momentum_analyst":      LLMTier.STANDARD,
    "room1.committee_chair":       LLMTier.STANDARD,
    # Room 2
    "room2.portfolio_cartographer": LLMTier.REASONING,
    "room2.liquidation_strategist": LLMTier.REASONING,
    "room2.opportunity_cost":      LLMTier.REASONING,
    "room2.position_sizer":        LLMTier.REASONING,
    "room2.cost_basis_accountant": LLMTier.REASONING,
    "room2.allocation_chair":      LLMTier.REASONING,
    # Room 3
    "room3.risk_gatekeeper":       LLMTier.REASONING,
    "room3.tail_risk_sentinel":    LLMTier.REASONING,
    "room3.market_timer":          LLMTier.STANDARD,
    "room3.execution_trader":      LLMTier.FAST,
    "room3.post_trade_auditor":    LLMTier.STANDARD,
    # Supervisor
    "supervisor.firm_cio":         LLMTier.DEEP,
    "supervisor.alpha_decay":      LLMTier.STANDARD,
    "supervisor.crowding":         LLMTier.FAST,
    # Feedback
    "feedback.system_review":      LLMTier.DEEP,
    "feedback.trade_attribution":  LLMTier.STANDARD,
    "feedback.parameter_optimizer": LLMTier.REASONING,
    "feedback.agent_calibration":  LLMTier.STANDARD,
    # Intelligence
    "intelligence.strategy_memory": LLMTier.EMBEDDING,
}


def get_tier(agent_id: str) -> LLMTier:
    """Return tier for an agent; defaults to STANDARD if not explicitly mapped."""
    return AGENT_TIER_MAP.get(agent_id, LLMTier.STANDARD)
