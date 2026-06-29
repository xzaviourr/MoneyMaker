"""PositionSizer — Kelly-adjusted position sizing with pillar budget and correlation constraints."""
from __future__ import annotations

from typing import Any

import structlog

from ...shared.schemas import IdeaVerdict
from ...supervisor.capital_tracker import CapitalTracker

log = structlog.get_logger(__name__)


class PositionSizer:
    agent_id = "room2.position_sizer"

    # Tier multipliers for Kelly fraction
    _TIER_FACTOR = {"full": 1.0, "half": 0.5, "starter": 0.25}

    async def compute(
        self,
        verdict: IdeaVerdict,
        bull_case: dict[str, Any],
        bear_case: dict[str, Any],
        cartographer: dict[str, Any],
        current_price: float,
    ) -> dict[str, Any]:
        tracker = CapitalTracker.get()
        snap    = await tracker.snapshot()

        lt_pillar = snap.pillar_allocations.get("long_term")
        pillar_available = float(lt_pillar.available) if lt_pillar else 0.0
        if pillar_available <= 0:
            return {"error": "no_capital_available", "position_value_inr": 0.0, "quantity": 0}

        conviction   = verdict.final_conviction
        upside_pct   = bull_case.get("price_target_pct_upside", 10.0) / 100
        downside_pct = bear_case.get("max_downside_pct", 5.0) / 100

        # Kelly fraction: f* = (bp - q) / b where b=upside/downside, p=conviction
        if downside_pct > 0:
            b       = upside_pct / downside_pct
            kelly_f = (b * conviction - (1 - conviction)) / b
        else:
            kelly_f = conviction * 0.1

        kelly_f = max(0.0, min(kelly_f, 0.25))  # half-Kelly cap at 25%

        # Apply tier multiplier
        tier_factor = self._TIER_FACTOR.get(verdict.position_tier, 0.25)
        final_frac  = kelly_f * tier_factor

        # Capped by cartographer recommended max
        cart_max = float(cartographer.get("max_position_pct_recommended", 5.0)) / 100
        final_frac = min(final_frac, cart_max)

        position_value = pillar_available * final_frac
        quantity       = int(position_value / current_price) if current_price > 0 else 0

        log.info("position_sizer.result",
                 symbol=verdict.symbol, kelly=kelly_f, final_frac=final_frac,
                 value=position_value, qty=quantity)

        return {
            "kelly_fraction":   kelly_f,
            "applied_fraction": final_frac,
            "position_value_inr": position_value,
            "quantity":         quantity,
            "pillar_available": pillar_available,
            "tier_factor":      tier_factor,
        }
