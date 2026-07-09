"""Decisions routes — query the ExplainabilityLedger."""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Query, HTTPException

from ...audit.explainability_ledger import ExplainabilityLedger

router = APIRouter()

_SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")
_AGENT_RE  = re.compile(r"^[a-z0-9_]{1,40}$")
_MODE_RE   = re.compile(r"^(demo|paper)$")


@router.get("/")
async def get_decisions(
    symbol:   Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    mode:     Optional[str] = Query(None),
    limit:    int            = Query(50, le=500),
) -> list[dict]:
    if symbol and not _SYMBOL_RE.match(symbol):
        raise HTTPException(status_code=422, detail="Invalid symbol format")
    if agent_id and not _AGENT_RE.match(agent_id):
        raise HTTPException(status_code=422, detail="Invalid agent_id format")
    if mode and not _MODE_RE.match(mode):
        raise HTTPException(status_code=422, detail="mode must be 'demo' or 'paper'")
    ledger = ExplainabilityLedger.get()
    return await ledger.query(symbol=symbol, agent_id=agent_id, mode=mode, limit=limit)
