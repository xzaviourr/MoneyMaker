"""Decisions routes — query the ExplainabilityLedger."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from ...intelligence.explainability_ledger import ExplainabilityLedger

router = APIRouter()


@router.get("/")
async def get_decisions(
    symbol:   Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    limit:    int            = Query(50, le=500),
) -> list[dict]:
    ledger = ExplainabilityLedger.get()
    return await ledger.query(symbol=symbol, agent_id=agent_id, limit=limit)
