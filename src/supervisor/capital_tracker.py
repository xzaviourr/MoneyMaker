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
