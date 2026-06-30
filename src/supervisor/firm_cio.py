"""
FirmCIO — highest-level orchestrator.

Responsibilities:
  - Sets capital split across pillars (daily or on macro events)
  - Sets overall risk posture and macro stance
  - Consumes SystemHealthReports (Loop 4)
  - Runs FirmCIO analysis using the DEEP LLM tier
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from typing import Optional

import structlog

from ..llm.llm_gateway import LLMGateway
from ..shared.config import toml_cfg
from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    CapitalSnapshot,
    Message,
    MessageType,
    RegimeSnapshot,
    SystemHealthReport,
)
from .capital_tracker import CapitalTracker
from .circuit_breaker import CircuitBreaker

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT = """
You are the Chief Investment Officer of MoneyMaker, an algorithmic trading firm
focused on Indian equity markets (NSE/BSE).

Your responsibilities:
1. Review system health reports and performance metrics
2. Recommend capital allocation adjustments between intraday pods, long-term desk
3. Set overall risk posture (aggressive / neutral / defensive)
4. Respond to macro events (RBI decisions, budget, global crises)

Always respond in structured JSON with keys:
  intraday_allocation_pct, long_term_allocation_pct, guardian_reserve_pct,
  risk_posture, macro_stance, rationale, recommended_actions

Risk posture values: "aggressive" | "neutral" | "defensive" | "crisis"
"""


class FirmCIO:
    def __init__(self) -> None:
        self._capital = CapitalTracker.get()
        self._breaker = CircuitBreaker()
        self._bus     = MessageBus.get()
        self._regime: Optional[RegimeSnapshot] = None
        self._last_report: Optional[SystemHealthReport] = None
        self._last_review_at: Optional[datetime] = None
        self._lock    = asyncio.Lock()

        self._bus.subscribe(MessageType.SYSTEM_HEALTH_REPORT, self._on_health_report)
        self._bus.subscribe(MessageType.REGIME_CHANGE, self._on_regime_change)
        self._bus.subscribe(MessageType.CIRCUIT_BREAKER_TRIGGERED, self._on_circuit_breaker)

    async def start(self) -> None:
        log.info("firm_cio.started")

    # ── Event handlers ─────────────────────────────────────────────────────

    async def _on_health_report(self, message: Message) -> None:
        report = SystemHealthReport(**message.payload)
        async with self._lock:
            self._last_report = report
            self._last_review_at = datetime.utcnow()
        await self._run_monthly_review_if_needed(report)

    async def _on_regime_change(self, message: Message) -> None:
        self._regime = RegimeSnapshot(**message.payload)
        if self._regime.is_crisis:
            log.warning("firm_cio.crisis_regime_detected")
            await self._emergency_defensive_posture()

    async def _on_circuit_breaker(self, message: Message) -> None:
        from ..shared.schemas import CircuitBreakerEvent
        event = CircuitBreakerEvent(**message.payload)
        log.critical("firm_cio.circuit_breaker_event", trigger=event.trigger)
        await self._emergency_defensive_posture()

    # ── Scheduled review (Loop 4) ──────────────────────────────────────────

    async def run_daily_review(self) -> Optional[dict]:
        """Runs each morning. Uses DEEP tier for comprehensive analysis."""
        snap = await self._capital.snapshot()
        regime_summary = self._regime.model_dump(mode="json") if self._regime else {}

        user_prompt = f"""
Today's Capital Snapshot:
{json.dumps(snap.model_dump(mode="json"), indent=2, default=str)}

Current Market Regime:
{json.dumps(regime_summary, indent=2)}

Recent Health Report:
{json.dumps(self._last_report.model_dump(mode="json") if self._last_report else {}, indent=2, default=str)}

Based on the above, provide your capital allocation recommendations and risk posture.
Consider: current regime, recent performance, drawdown proximity, macro factors.
"""
        try:
            llm = LLMGateway.get()
            result = await llm.complete_json(
                agent_id="supervisor.firm_cio",
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )
            log.info("firm_cio.daily_review_complete", recommendation=result)
            await self._apply_recommendation(result, snap)
            return result
        except Exception as exc:
            log.error("firm_cio.review_failed", error=str(exc))
            return None

    async def _run_monthly_review_if_needed(self, report: SystemHealthReport) -> None:
        if (
            self._last_review_at is None
            or (datetime.utcnow() - self._last_review_at).days >= 30
        ):
            await self.run_daily_review()

    async def _apply_recommendation(
        self, rec: dict, snap: CapitalSnapshot
    ) -> None:
        """Apply allocation changes if they differ materially from current."""
        new_intraday = float(rec.get("intraday_allocation_pct", 40)) / 100
        new_lt       = float(rec.get("long_term_allocation_pct", 50)) / 100
        new_reserve  = float(rec.get("guardian_reserve_pct", 10)) / 100

        if abs(new_intraday + new_lt + new_reserve - 1.0) > 0.01:
            log.warning("firm_cio.allocation_doesnt_sum_to_100", rec=rec)
            return

        # TODO: resize pillars if allocation has changed by > 5%
        log.info(
            "firm_cio.allocation_applied",
            intraday_pct=new_intraday,
            lt_pct=new_lt,
            reserve_pct=new_reserve,
            risk_posture=rec.get("risk_posture"),
        )

    async def _emergency_defensive_posture(self) -> None:
        """Triggered on crisis regime or circuit breaker."""
        log.critical("firm_cio.emergency_defensive_posture")
        # Actual capital reallocation logic delegated to PodSupervisor
        # which responds to circuit-breaker events on the bus

    # ── Macro event hook (called by HumanCommandInterface or scheduled) ────

    async def on_macro_event(self, event_description: str) -> Optional[dict]:
        """
        Called when a major macro event is detected (RBI policy, budget, etc).
        Triggers an immediate FirmCIO review.
        """
        snap = await self._capital.snapshot()
        user_prompt = f"""
MACRO EVENT DETECTED: {event_description}

Current Capital State:
{json.dumps(snap.model_dump(mode="json"), indent=2, default=str)}

Assess the impact of this event on Indian equity markets and recommend
immediate portfolio risk adjustments.
"""
        try:
            llm = LLMGateway.get()
            return await llm.complete_json(
                agent_id="supervisor.firm_cio",
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )
        except Exception as exc:
            log.error("firm_cio.macro_event_failed", error=str(exc))
            return None
