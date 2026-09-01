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


@router.get("/rejected-tracking")
async def get_rejected_tracking(limit: int = Query(200, le=1000)) -> dict:
    """Rejected ideas with a recorded price check — what happened to what we
    passed on. 'would_have_profited' only counts rows with at least one
    price check; still-open rejections (last_price is null) are excluded
    from the hit-rate math since we don't know their outcome yet."""
    ledger = ExplainabilityLedger.get()
    rows = await ledger.query_rejected_tracking(limit=limit)

    checked = [r for r in rows if r["pct_change"] is not None]
    profitable = [r for r in checked if r["pct_change"] > 0]
    hit_rate = (len(profitable) / len(checked) * 100) if checked else None

    by_room: dict[str, dict] = {}
    for r in checked:
        room = r["room"] or "unknown"
        bucket = by_room.setdefault(room, {"total": 0, "profitable": 0})
        bucket["total"] += 1
        if r["pct_change"] > 0:
            bucket["profitable"] += 1
    room_summary = [
        {"room": room, "total": b["total"], "profitable": b["profitable"],
         "hit_rate": round(b["profitable"] / b["total"] * 100, 1)}
        for room, b in by_room.items()
    ]

    return {
        "rows": rows,
        "summary": {
            "checked": len(checked),
            "profitable": len(profitable),
            "hit_rate": round(hit_rate, 1) if hit_rate is not None else None,
            "by_room": room_summary,
        },
    }
