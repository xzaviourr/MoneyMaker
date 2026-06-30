"""
CircuitBreaker — firm-wide automatic halt based on drawdown thresholds.

Trigger table (from architecture §3):
  System down > 2%  on day  → halt intraday pods, reduce LT activity
  System down > 4%  on day  → halt everything; only Guardian can act
  3 consecutive losing days → FirmCIO triggers emergency portfolio review
  VIX spikes > 30% in 1h   → all pods pause; Guardian active defence
"""
from __future__ import annotations

import asyncio
from datetime import datetime, date
from decimal import Decimal
from typing import Optional

import structlog

from ..shared.config import toml_cfg
from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    CircuitBreakerEvent,
    CircuitBreakerState,
    Message,
    MessageType,
    RegimeSnapshot,
    VolatilityLevel,
)

log = structlog.get_logger(__name__)


class CircuitBreaker:
    _instance: "CircuitBreaker | None" = None

    @classmethod
    def get(cls) -> "CircuitBreaker":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        cfg = toml_cfg.get("circuit_breaker", {})
        self._halt_pct       = float(cfg.get("daily_halt_pct", 2.0))
        self._emergency_pct  = float(cfg.get("emergency_halt_pct", 4.0))
        self._consec_days    = int(cfg.get("consecutive_loss_days", 3))
        self._vix_spike_pct  = float(cfg.get("vix_spike_pct", 30.0))

        self._state          = CircuitBreakerState.NORMAL
        self._daily_pnl      = Decimal("0")
        self._total_capital  = Decimal("1000000")
        self._daily_pnl_history: list[tuple[date, Decimal]] = []
        self._last_vix:     Optional[float] = None
        self._bus            = MessageBus.get()
        self._lock           = asyncio.Lock()
        self._pods_halted    = False
        self._all_halted     = False

    # ── Called by CapitalTracker on every P&L update ────────────────────────

    async def update_pnl(self, daily_pnl: Decimal, total_capital: Decimal) -> None:
        async with self._lock:
            self._daily_pnl     = daily_pnl
            self._total_capital = total_capital
            await self._evaluate()

    async def update_regime(self, regime: RegimeSnapshot) -> None:
        async with self._lock:
            if regime.vix is not None:
                old_vix = self._last_vix
                self._last_vix = regime.vix
                if (
                    old_vix is not None
                    and old_vix > 0
                    and (regime.vix - old_vix) / old_vix * 100 >= self._vix_spike_pct
                ):
                    await self._trigger(
                        f"VIX spiked from {old_vix:.1f} to {regime.vix:.1f}",
                        CircuitBreakerState.TRIPPED,
                        action="pause_all_pods_guardian_defence",
                    )
            if regime.is_crisis:
                await self._trigger(
                    "Crisis volatility regime detected",
                    CircuitBreakerState.WARNING,
                    action="reduce_position_sizes",
                )

    async def record_eod_pnl(self, pnl: Decimal) -> None:
        """Call at end of trading day."""
        async with self._lock:
            self._daily_pnl_history.append((date.today(), pnl))
            # Keep last 30 days
            self._daily_pnl_history = self._daily_pnl_history[-30:]
            # Reset daily P&L
            self._daily_pnl = Decimal("0")
            # Check consecutive losing days
            recent = [d for _, d in self._daily_pnl_history[-self._consec_days:]]
            if len(recent) == self._consec_days and all(d < 0 for d in recent):
                await self._trigger(
                    f"{self._consec_days} consecutive losing days",
                    CircuitBreakerState.WARNING,
                    action="emergency_portfolio_review",
                )

    async def reset(self) -> None:
        async with self._lock:
            self._state       = CircuitBreakerState.NORMAL
            self._pods_halted = False
            self._all_halted  = False
            log.info("circuit_breaker.reset")
            await self._bus.publish(
                Message(
                    type=MessageType.CIRCUIT_BREAKER_RESET,
                    source="circuit_breaker",
                    payload={"timestamp": datetime.utcnow().isoformat()},
                )
            )

    # ── Properties ─────────────────────────────────────────────────────────

    @property
    def state(self) -> CircuitBreakerState:
        return self._state

    @property
    def pods_halted(self) -> bool:
        return self._pods_halted

    @property
    def all_halted(self) -> bool:
        return self._all_halted

    def is_halted(self) -> bool:
        return self._all_halted or self._pods_halted

    async def start(self) -> None:
        """Subscribe to regime events for VIX monitoring."""
        from ..shared.schemas import MessageType
        self._bus.subscribe(MessageType.REGIME_CHANGE, self._on_regime)
        log.info("circuit_breaker.started")

    async def _on_regime(self, msg) -> None:
        try:
            from ..shared.schemas import RegimeSnapshot
            regime = RegimeSnapshot(**msg.payload)
            await self.update_regime(regime)
        except Exception:
            pass

    # ── Internal ───────────────────────────────────────────────────────────

    async def _evaluate(self) -> None:
        if self._total_capital == 0:
            return
        pnl_pct = float(self._daily_pnl / self._total_capital * 100)

        if pnl_pct <= -self._emergency_pct:
            await self._trigger(
                f"Daily P&L {pnl_pct:.2f}% hit emergency threshold -{self._emergency_pct}%",
                CircuitBreakerState.EMERGENCY,
                action="halt_everything",
            )
        elif pnl_pct <= -self._halt_pct:
            await self._trigger(
                f"Daily P&L {pnl_pct:.2f}% hit halt threshold -{self._halt_pct}%",
                CircuitBreakerState.TRIPPED,
                action="halt_intraday_pods",
            )

    async def _trigger(
        self, reason: str, state: CircuitBreakerState, action: str
    ) -> None:
        if self._state == state:
            return  # already in this state

        self._state = state
        if action == "halt_everything":
            self._all_halted  = True
            self._pods_halted = True
        elif action in ("halt_intraday_pods", "pause_all_pods_guardian_defence"):
            self._pods_halted = True

        event = CircuitBreakerEvent(
            trigger=reason,
            state=state,
            daily_pnl_pct=float(self._daily_pnl / self._total_capital * 100),
            action_taken=action,
        )
        log.critical(
            "circuit_breaker.triggered",
            reason=reason,
            state=state.value,
            action=action,
        )
        await self._bus.publish(
            Message(
                type=MessageType.CIRCUIT_BREAKER_TRIGGERED,
                source="circuit_breaker",
                payload=event.model_dump(mode="json"),
            )
        )
