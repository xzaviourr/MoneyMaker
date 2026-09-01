"""RiskGatekeeper — Final risk checks before execution: VaR, correlation limits, portfolio heat."""
from __future__ import annotations

from typing import Any

import structlog

from ...brokers.broker_gateway import BrokerGateway
from ...shared.config import toml_cfg
from ...shared.schemas import AllocationPlan
from ...supervisor.circuit_breaker import CircuitBreaker

log = structlog.get_logger(__name__)

# A portfolio's own config.toml ([risk_gate] section) can override any of
# these to build a more conservative or more aggressive persona — e.g. a
# stricter _MIN_RR_RATIO and lower _MAX_CORR_WITH_EXISTING for a cautious
# portfolio, or looser values for a bold one. Defaults match the values
# tuned on 2026-07-13, when a stricter 1.5 R:R bar produced 0 trades across
# 120+ debated ideas in a full day; most rejections landed at 1.25-1.47.
_risk_cfg = toml_cfg.get("risk_gate", {})
_MAX_SINGLE_POSITION_PCT = float(_risk_cfg.get("max_single_position_pct", 10.0))
_MAX_CORR_WITH_EXISTING  = float(_risk_cfg.get("max_correlation", 0.85))
_MIN_RR_RATIO            = float(_risk_cfg.get("min_risk_reward", 1.2))


class RiskGatekeeper:
    agent_id = "room3.risk_gatekeeper"

    async def check(
        self,
        plan: AllocationPlan,
        opportunity_cost: dict[str, Any],
        cartographer: dict[str, Any],
        pillar_total: float,
    ) -> dict[str, Any]:
        breaker = CircuitBreaker.get()
        issues  = []
        blocked = False

        # Circuit breaker state
        if breaker.is_halted():
            issues.append("circuit_breaker_active")
            blocked = True

        # Position concentration check — must include what's already held in
        # this exact symbol, not just the new tranche being proposed. Each
        # individual buy used to look small and safe on its own, so the same
        # stock could get bought repeatedly across separate debate cycles
        # (the "correlation" check below judges different stocks moving
        # together, an LLM call — it never verified the deterministic fact
        # of "how much of this exact symbol do I already own," so COALINDIA
        # grew to 41% of the long-term pool on 2026-07-20 with every single
        # tranche individually passing this gate).
        existing_value = 0.0
        try:
            positions = await BrokerGateway.get().get_positions()
            existing_value = sum(
                float(p.quantity) * float(p.current_price)
                for p in positions if p.symbol == plan.symbol
            )
        except Exception:
            pass
        combined_value = existing_value + plan.allocated_capital
        pos_pct = (combined_value / pillar_total * 100) if pillar_total > 0 else 0
        if pos_pct > _MAX_SINGLE_POSITION_PCT:
            issues.append(
                f"position_too_large: {pos_pct:.1f}% > {_MAX_SINGLE_POSITION_PCT}% "
                f"(already hold ₹{existing_value:,.0f} of {plan.symbol})"
            )
            blocked = True

        # Correlation risk from cartographer
        corr_risk = cartographer.get("correlation_risk", "low")
        if corr_risk == "high":
            issues.append("high_correlation_with_existing_positions")
            blocked = True

        # Risk-reward ratio from opportunity cost analyst
        rr = float(opportunity_cost.get("risk_reward_ratio", 0))
        if rr > 0 and rr < _MIN_RR_RATIO:
            issues.append(f"rr_below_minimum: {rr:.2f} < {_MIN_RR_RATIO}")
            blocked = True

        result = {
            "passed":       not blocked,
            "blocked":      blocked,
            "issues":       issues,
            "position_pct": pos_pct,
        }
        log.info("risk_gatekeeper.result",
                 symbol=plan.symbol, passed=not blocked, issues=issues)
        return result
