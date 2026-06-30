"""RiskGatekeeper — Final risk checks before execution: VaR, correlation limits, portfolio heat."""
from __future__ import annotations

from typing import Any

import structlog

from ...shared.schemas import AllocationPlan
from ...supervisor.circuit_breaker import CircuitBreaker

log = structlog.get_logger(__name__)

# Hard limits
_MAX_SINGLE_POSITION_PCT = 10.0   # max 10% of LT pillar in one position
_MAX_CORR_WITH_EXISTING  = 0.85   # block if new position corr > 85% with existing
_MIN_RR_RATIO            = 1.5    # minimum risk-reward to proceed


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

        # Position concentration check
        pos_pct = (plan.allocated_capital / pillar_total * 100) if pillar_total > 0 else 0
        if pos_pct > _MAX_SINGLE_POSITION_PCT:
            issues.append(f"position_too_large: {pos_pct:.1f}% > {_MAX_SINGLE_POSITION_PCT}%")
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
