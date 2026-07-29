"""
CapitalTracker — real-time ledger wrapper with a hard no-overdraw guarantee.
Every capital movement in the system goes through here.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Optional

import structlog

from ..shared.capital_ledger import CapitalLedger, CapitalError
from ..shared.config import toml_cfg
from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    CapitalSnapshot,
    Message,
    MessageType,
    PillarAllocation,
)

log = structlog.get_logger(__name__)


class CapitalTracker:
    """
    Singleton. Initialise at startup with total capital; pillars are created
    from config fractions.
    """

    _instance: Optional["CapitalTracker"] = None

    def __init__(self) -> None:
        cfg = toml_cfg.get("capital", {})
        self._total = Decimal(str(cfg.get("total_capital", 1_000_000)))
        self._intraday_pct   = float(cfg.get("intraday_pct", 0.40))
        self._lt_pct         = float(cfg.get("long_term_pct", 0.50))
        self._reserve_pct    = float(cfg.get("guardian_reserve_pct", 0.10))
        self._ledger = CapitalLedger(self._total)
        self._bus    = MessageBus.get()

    @classmethod
    def get(cls) -> "CapitalTracker":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def initialise(self) -> None:
        """Must be called once at system startup."""
        await self._ledger.create_pillar(
            "intraday", self._total * Decimal(str(self._intraday_pct))
        )
        await self._ledger.create_pillar(
            "long_term", self._total * Decimal(str(self._lt_pct))
        )
        await self._ledger.create_pillar(
            "guardian_reserve", self._total * Decimal(str(self._reserve_pct))
        )
        log.info(
            "capital_tracker.initialised",
            total=str(self._total),
            intraday=str(self._total * Decimal(str(self._intraday_pct))),
            long_term=str(self._total * Decimal(str(self._lt_pct))),
        )

    # ── Pod lifecycle ──────────────────────────────────────────────────────

    async def allocate_to_pod(
        self, pillar: str, pod_id: str, amount: Decimal
    ) -> Decimal:
        try:
            total = await self._ledger.allocate_to_pod(pillar, pod_id, amount)
            await self._bus.publish(
                Message(
                    type=MessageType.CAPITAL_ALLOCATED,
                    source="capital_tracker",
                    payload={"pod_id": pod_id, "pillar": pillar, "amount": str(amount)},
                )
            )
            return total
        except CapitalError as exc:
            log.error("capital_tracker.overdraw_blocked", error=str(exc))
            raise

    async def return_from_pod(
        self, pillar: str, pod_id: str, amount: Decimal, pnl: Decimal = Decimal("0")
    ) -> None:
        await self._ledger.return_from_pod(pillar, pod_id, amount, pnl)
        await self._bus.publish(
            Message(
                type=MessageType.CAPITAL_RETURNED,
                source="capital_tracker",
                payload={
                    "pod_id": pod_id,
                    "pillar": pillar,
                    "amount": str(amount),
                    "pnl": str(pnl),
                },
            )
        )

    # ── Snapshots ──────────────────────────────────────────────────────────

    async def snapshot(self) -> CapitalSnapshot:
        return await self._ledger.snapshot()

    def get_pod_allocation(self, pod_id: str) -> Decimal:
        return self._ledger.get_pod_allocation(pod_id)

    async def available_in_pillar(self, pillar: str) -> Decimal:
        snap = await self._ledger.snapshot()
        p = snap.pillar_allocations.get(pillar)
        return p.available if p else Decimal("0")

    async def reserve_for_lt_desk(self, symbol: str, amount: float) -> None:
        """Reserve capital in the long_term pillar for an LT desk trade."""
        dec_amount = Decimal(str(round(amount, 2)))
        await self.allocate_to_pod(
            pillar="long_term",
            pod_id=f"lt_desk_{symbol}",
            amount=dec_amount,
        )

    async def release_lt_desk(self, symbol: str, amount: float, pnl: float = 0.0) -> None:
        """Release reserved LT desk capital (on exit or cancel)."""
        dec_amount = Decimal(str(round(amount, 2)))
        dec_pnl    = Decimal(str(round(pnl, 2)))
        await self.return_from_pod(
            pillar="long_term",
            pod_id=f"lt_desk_{symbol}",
            amount=dec_amount,
            pnl=dec_pnl,
        )

    # ── Reconciliation ─────────────────────────────────────────────────────
    # Found 07-28: the ledger above is purely in-memory (unlike the broker's
    # own state, which persists to disk) and every allocate/return call has
    # to be matched exactly for its numbers to stay true — one missed
    # release call, or simply restarting the process, silently drifts it
    # away from reality forever. Confirmed live: after normal restarts, the
    # ledger showed long_term at ₹0 deployed / ₹5L available while ₹9.8L was
    # actually invested, and intraday showed its full ₹4L "deployed" while
    # pods had never placed a single real trade. Fix: periodically recompute
    # each pillar's real deployed capital directly from actual open broker
    # positions — the one thing that's actually persisted and can't lie —
    # so any drift self-corrects instead of compounding indefinitely.

    _INTRADAY_POD_IDS = {"momentum_pod", "breakout_pod", "mean_reversion_pod", "event_pod"}

    async def reconcile_with_broker(self) -> None:
        from ..brokers.broker_gateway import BrokerGateway
        try:
            positions = await BrokerGateway.get().get_positions()
        except Exception:
            log.warning("capital_tracker.reconcile_failed_no_broker")
            return

        lt_value       = Decimal("0")
        intraday_value = Decimal("0")
        for p in positions:
            value = Decimal(str(p.current_price)) * p.quantity
            if p.source_desk == "long_term_desk":
                lt_value += value
            elif p.source_pod in self._INTRADAY_POD_IDS:
                intraday_value += value

        await self._ledger.set_deployed("long_term", lt_value)
        await self._ledger.set_deployed("intraday", intraday_value)
        log.info("capital_tracker.reconciled",
                 long_term_deployed=str(lt_value), intraday_deployed=str(intraday_value))

    async def start_reconcile_loop(self) -> None:
        await self.reconcile_with_broker()  # correct immediately, not just eventually
        asyncio.create_task(self._reconcile_loop())

    async def _reconcile_loop(self) -> None:
        while True:
            await asyncio.sleep(300)
            try:
                await self.reconcile_with_broker()
            except Exception:
                log.exception("capital_tracker.reconcile_loop_error")
