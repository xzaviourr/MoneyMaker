"""
Thread-safe, async capital ledger.
CapitalTracker (supervisor layer) wraps this; nothing else touches it directly.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Optional

import structlog

from .schemas import CapitalSnapshot, PillarAllocation

log = structlog.get_logger(__name__)


class CapitalLedger:
    """
    Immutable-style ledger: every allocation returns the updated available balance.
    Raises CapitalError on overdraw — no pillar can go negative.
    """

    def __init__(self, total_capital: Decimal) -> None:
        self._lock = asyncio.Lock()
        self._total = total_capital
        self._available = total_capital
        self._pillars: dict[str, PillarAllocation] = {}
        self._pod_allocations: dict[str, Decimal] = {}    # pod_id → allocated
        self._pod_deployed: dict[str, Decimal] = {}       # pod_id → deployed

    # ── Pillar management ──────────────────────────────────────────────────

    async def create_pillar(self, pillar: str, amount: Decimal) -> None:
        async with self._lock:
            if amount > self._available:
                raise CapitalError(
                    f"Cannot allocate ₹{amount} to pillar '{pillar}': "
                    f"only ₹{self._available} available"
                )
            self._available -= amount
            self._pillars[pillar] = PillarAllocation(
                pillar=pillar,
                allocated=amount,
                deployed=Decimal("0"),
                available=amount,
            )
            log.info("ledger.pillar_created", pillar=pillar, amount=str(amount))

    async def resize_pillar(self, pillar: str, new_amount: Decimal) -> None:
        async with self._lock:
            alloc = self._pillars[pillar]
            delta = new_amount - alloc.allocated
            if delta > self._available:
                raise CapitalError(
                    f"Cannot grow pillar '{pillar}' by ₹{delta}: "
                    f"only ₹{self._available} available"
                )
            self._available -= delta
            self._pillars[pillar] = alloc.model_copy(
                update={"allocated": new_amount, "available": alloc.available + delta}
            )

    # ── Pod-level allocation ───────────────────────────────────────────────

    async def allocate_to_pod(self, pillar: str, pod_id: str, amount: Decimal) -> Decimal:
        async with self._lock:
            alloc = self._pillars[pillar]
            if amount > alloc.available:
                raise CapitalError(
                    f"Pillar '{pillar}' has only ₹{alloc.available} available; "
                    f"requested ₹{amount} for pod '{pod_id}'"
                )
            self._pillars[pillar] = alloc.model_copy(
                update={
                    "deployed": alloc.deployed + amount,
                    "available": alloc.available - amount,
                }
            )
            self._pod_allocations[pod_id] = (
                self._pod_allocations.get(pod_id, Decimal("0")) + amount
            )
            log.info(
                "ledger.pod_allocated",
                pillar=pillar, pod_id=pod_id, amount=str(amount),
            )
            return self._pod_allocations[pod_id]

    async def return_from_pod(
        self, pillar: str, pod_id: str, amount: Decimal, pnl: Decimal = Decimal("0")
    ) -> None:
        async with self._lock:
            alloc = self._pillars[pillar]
            return_amount = amount + pnl
            self._pillars[pillar] = alloc.model_copy(
                update={
                    "deployed": max(Decimal("0"), alloc.deployed - amount),
                    "available": alloc.available + return_amount,
                    "pnl": alloc.pnl + pnl,
                }
            )
            current = self._pod_allocations.get(pod_id, Decimal("0"))
            self._pod_allocations[pod_id] = max(Decimal("0"), current - amount)
            log.info(
                "ledger.pod_returned",
                pillar=pillar, pod_id=pod_id,
                amount=str(amount), pnl=str(pnl),
            )

    # ── Snapshots ──────────────────────────────────────────────────────────

    async def snapshot(self) -> CapitalSnapshot:
        async with self._lock:
            deployed = sum(a.deployed for a in self._pillars.values())
            total_pnl = sum(a.pnl for a in self._pillars.values())
            return CapitalSnapshot(
                total_capital=self._total + total_pnl,
                available_capital=self._available,
                deployed_capital=deployed,
                reserved_capital=Decimal("0"),
                total_pnl=total_pnl,
                pillar_allocations={k: v for k, v in self._pillars.items()},
            )

    def get_pod_allocation(self, pod_id: str) -> Decimal:
        return self._pod_allocations.get(pod_id, Decimal("0"))

    # ── Direct withdraw/deposit (Guardian use) ─────────────────────────────

    async def emergency_reserve(self, amount: Decimal) -> None:
        """Guardian sets aside capital during crisis."""
        async with self._lock:
            if amount > self._available:
                raise CapitalError(f"Cannot reserve ₹{amount}: insufficient available capital")
            self._available -= amount

    async def release_reserve(self, amount: Decimal) -> None:
        async with self._lock:
            self._available += amount


class CapitalError(Exception):
    """Raised when a capital operation would cause an overdraw."""
