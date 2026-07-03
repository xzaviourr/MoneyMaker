"""Tests for CapitalLedger — the no-overdraw guarantee is system-critical."""
import pytest
from decimal import Decimal
from src.shared.capital_ledger import CapitalLedger, CapitalError


@pytest.fixture
async def ledger():
    l = CapitalLedger(Decimal("1_000_000"))
    await l.create_pillar("intraday", Decimal("400_000"))
    await l.create_pillar("long_term", Decimal("500_000"))
    return l


async def test_allocate_reduces_available(ledger):
    await ledger.allocate_to_pod("intraday", "pod_a", Decimal("100_000"))
    snap = await ledger.snapshot()
    assert snap.pillar_allocations["intraday"].available == Decimal("300_000")


async def test_overdraw_raises(ledger):
    with pytest.raises(CapitalError):
        await ledger.allocate_to_pod("intraday", "pod_a", Decimal("500_000"))


async def test_return_restores_available(ledger):
    await ledger.allocate_to_pod("intraday", "pod_a", Decimal("100_000"))
    await ledger.return_from_pod("intraday", "pod_a", Decimal("100_000"), Decimal("1_000"))
    snap = await ledger.snapshot()
    assert snap.pillar_allocations["intraday"].available == Decimal("401_000")


async def test_unknown_pillar_raises(ledger):
    with pytest.raises(CapitalError, match="Unknown pillar"):
        await ledger.allocate_to_pod("nonexistent", "pod_a", Decimal("1_000"))


async def test_pod_allocation_tracked(ledger):
    await ledger.allocate_to_pod("intraday", "pod_a", Decimal("50_000"))
    assert ledger.get_pod_allocation("pod_a") == Decimal("50_000")
