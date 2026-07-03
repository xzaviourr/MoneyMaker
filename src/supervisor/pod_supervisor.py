"""
PodSupervisor — manages pod lifecycle, capital budgets, performance tracking.

Lifecycle: SANDBOX → PROBATION → LIVE → REVIEW → KILLED
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

import structlog

from ..shared.config import toml_cfg
from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    Message,
    MessageType,
    PodConfig,
    PodMetrics,
    PodState,
    RegimeSnapshot,
)
from .alpha_decay_monitor import AlphaDecayMonitor
from .capital_tracker import CapitalTracker

if TYPE_CHECKING:
    from ..pods.base_pod import BasePod

log = structlog.get_logger(__name__)


class PodSupervisor:
    def __init__(self) -> None:
        cfg = toml_cfg.get("pods", {})
        self._sandbox_min_days   = int(cfg.get("sandbox_min_days", 14))
        self._sandbox_min_pnl    = float(cfg.get("sandbox_min_pnl_pct", 1.0))
        self._probation_cap_pct  = float(cfg.get("probation_capital_pct", 0.01))

        self._pods: dict[str, "BasePod"]  = {}
        self._metrics: dict[str, PodMetrics] = {}
        self._last_seen: dict[str, datetime] = {}
        self._capital = CapitalTracker.get()
        self._decay   = AlphaDecayMonitor()
        self._bus     = MessageBus.get()
        self._lock    = asyncio.Lock()
        self._regime: Optional[RegimeSnapshot] = None

        # subscribe to regime changes to auto-pause incompatible pods
        self._bus.subscribe(MessageType.REGIME_CHANGE, self._on_regime_change)
        self._bus.subscribe(MessageType.CIRCUIT_BREAKER_TRIGGERED, self._on_circuit_breaker)

    # ── Registration ───────────────────────────────────────────────────────

    async def register_pod(self, pod: "BasePod") -> None:
        async with self._lock:
            self._pods[pod.pod_id] = pod
            self._metrics[pod.pod_id] = PodMetrics(pod_id=pod.pod_id)
        log.info("pod_supervisor.registered", pod_id=pod.pod_id, state=pod.state.value)

    async def deregister_pod(self, pod_id: str) -> None:
        async with self._lock:
            pod = self._pods.pop(pod_id, None)
        if pod:
            allocation = self._capital.get_pod_allocation(pod_id)
            if allocation > 0:
                metrics = self._metrics.get(pod_id)
                pnl = metrics.total_pnl if metrics else Decimal("0")
                await self._capital.return_from_pod("intraday", pod_id, allocation, pnl)
        log.info("pod_supervisor.deregistered", pod_id=pod_id)

    # ── Lifecycle transitions ──────────────────────────────────────────────

    async def try_graduate(self, pod_id: str) -> bool:
        """Attempt SANDBOX → PROBATION graduation."""
        pod = self._pods.get(pod_id)
        metrics = self._metrics.get(pod_id)
        if not pod or not metrics:
            return False
        if pod.state != PodState.SANDBOX:
            return False

        pnl_ok  = float(metrics.total_pnl) / 1 > self._sandbox_min_pnl
        days_ok = True  # TODO: track sandbox start date properly

        if pnl_ok and days_ok:
            await self._set_pod_state(pod_id, PodState.PROBATION)
            # Allocate small real capital
            pod_alloc = self._capital.get_pod_allocation(pod_id)
            # For now set to 1% of its future live budget
            return True
        return False

    async def promote_to_live(self, pod_id: str, full_budget: Decimal) -> None:
        pod = self._pods.get(pod_id)
        if not pod or pod.state != PodState.PROBATION:
            return
        await self._capital.allocate_to_pod("intraday", pod_id, full_budget)
        pod.config.capital_budget = full_budget
        await self._set_pod_state(pod_id, PodState.LIVE)

    async def send_to_review(self, pod_id: str, reason: str) -> None:
        pod = self._pods.get(pod_id)
        if not pod or pod.state != PodState.LIVE:
            return
        await self._set_pod_state(pod_id, PodState.REVIEW)
        log.warning("pod_supervisor.review", pod_id=pod_id, reason=reason)

    async def kill_pod(self, pod_id: str) -> None:
        pod = self._pods.get(pod_id)
        if not pod:
            return
        await pod.stop()
        allocation = self._capital.get_pod_allocation(pod_id)
        metrics = self._metrics.get(pod_id)
        pnl = metrics.total_pnl if metrics else Decimal("0")
        if allocation > 0:
            await self._capital.return_from_pod("intraday", pod_id, allocation, pnl)
        await self._set_pod_state(pod_id, PodState.KILLED)
        await self.deregister_pod(pod_id)

    # ── P&L tracking ───────────────────────────────────────────────────────

    async def update_metrics(self, pod_id: str, metrics: PodMetrics) -> None:
        async with self._lock:
            self._metrics[pod_id] = metrics
            self._last_seen[pod_id] = datetime.now(timezone.utc)

        # Check for alpha decay on every update
        if metrics.total_trades >= 20:
            await self._decay.record_daily_return(
                pod_id, float(metrics.daily_pnl)
            )
            analysis = await self._decay.analyse(pod_id)
            if analysis["recommendation"] == "demote":
                await self.send_to_review(pod_id, "AlphaDecayMonitor: structural decay")

    # ── Regime-aware pod management ────────────────────────────────────────

    async def _on_regime_change(self, message: Message) -> None:
        from ..shared.schemas import RegimeSnapshot
        regime = RegimeSnapshot(**message.payload)
        self._regime = regime

        async with self._lock:
            pods = dict(self._pods)

        for pod_id, pod in pods.items():
            if pod.state not in (PodState.LIVE, PodState.PROBATION):
                continue
            compatible = pod.config.compatible_regimes
            if compatible and regime.trend not in compatible:
                await pod.pause(f"Regime {regime.trend.value} incompatible")
                log.info(
                    "pod_supervisor.auto_paused",
                    pod_id=pod_id,
                    reason=f"Incompatible regime: {regime.trend.value}",
                )
            else:
                await pod.resume()

    async def _on_circuit_breaker(self, message: Message) -> None:
        from ..shared.schemas import CircuitBreakerEvent
        event = CircuitBreakerEvent(**message.payload)
        if event.action_taken in ("halt_everything", "halt_intraday_pods",
                                   "pause_all_pods_guardian_defence"):
            async with self._lock:
                pods = dict(self._pods)
            for pod in pods.values():
                await pod.pause(f"Circuit breaker: {event.trigger}")
            log.critical(
                "pod_supervisor.circuit_breaker_halt",
                action=event.action_taken,
                pod_count=len(pods),
            )

    # ── Helpers ────────────────────────────────────────────────────────────

    async def _set_pod_state(self, pod_id: str, state: PodState) -> None:
        pod = self._pods.get(pod_id)
        if pod:
            pod.config.state = state
        await self._bus.publish(
            Message(
                type=MessageType.POD_STATE_CHANGE,
                source="pod_supervisor",
                payload={"pod_id": pod_id, "new_state": state.value},
            )
        )

    def get_all_metrics(self) -> dict[str, PodMetrics]:
        return dict(self._metrics)

    def list_pods(self) -> list[str]:
        return list(self._pods.keys())

    @property
    def pods(self) -> dict:
        return self._pods

    async def start(self) -> None:
        """Subscribe to bus events and start health check loop."""
        asyncio.create_task(self._health_check_loop(), name="pod_health_check")
        log.info("pod_supervisor.started", pods=len(self._pods))

    async def _health_check_loop(self) -> None:
        """Every 60s: if a pod hasn't reported metrics in 2 min, warn and release its capital."""
        while True:
            await asyncio.sleep(60)
            now = datetime.now(timezone.utc)
            async with self._lock:
                pods = dict(self._pods)
            for pod_id, pod in pods.items():
                if pod.state in (PodState.KILLED, PodState.SANDBOX):
                    continue
                last = self._last_seen.get(pod_id)
                if last is None:
                    continue
                silent_secs = (now - last).total_seconds()
                if silent_secs > 120:
                    log.error(
                        "pod_supervisor.pod_silent",
                        pod_id=pod_id,
                        silent_seconds=int(silent_secs),
                    )
                    allocation = self._capital.get_pod_allocation(pod_id)
                    if allocation > 0:
                        metrics = self._metrics.get(pod_id)
                        pnl = metrics.total_pnl if metrics else Decimal("0")
                        await self._capital.return_from_pod("intraday", pod_id, allocation, pnl)
                        log.warning(
                            "pod_supervisor.capital_released_on_silence",
                            pod_id=pod_id, released=str(allocation),
                        )

    async def handle_command(self, pod_id: str, action: str) -> None:
        """Handle API lifecycle command for a pod."""
        pod = self._pods.get(pod_id)
        if not pod:
            return
        if action == "pause":
            await pod.pause("human_command")
        elif action == "resume":
            await pod.resume()
        elif action == "kill":
            await self._set_pod_state(pod_id, PodState.KILLED)
        elif action == "review":
            await self._set_pod_state(pod_id, PodState.REVIEW)
        log.info("pod_supervisor.command_handled", pod_id=pod_id, action=action)
