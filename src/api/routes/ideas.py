"""User-submitted trade ideas — "I saw this at 8pm, want to buy it tomorrow".

Runs through the same Room 1 AI debate as any auto-discovered idea so the
user sees real reasoning before deciding, but the verdict never blocks
execution — POST /ideas/{id}/execute buys regardless of what the AI concluded.
"""
from __future__ import annotations

import asyncio
import re

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...audit.explainability_ledger import ExplainabilityLedger
from .system import get_lt_desk

router = APIRouter()

_SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")


class SubmitIdeaRequest(BaseModel):
    symbol: str
    note:   str = ""


class ExecuteIdeaRequest(BaseModel):
    # None (or omitted body) means "use the AI's suggested quantity" — a
    # user-specified value always overrides it, never the other way round.
    quantity: Optional[int] = Field(default=None, gt=0)


@router.post("/")
async def submit_idea(body: SubmitIdeaRequest) -> dict:
    symbol = body.symbol.strip().upper()
    if not _SYMBOL_RE.match(symbol):
        raise HTTPException(status_code=422, detail="Invalid symbol format")

    lt_desk = get_lt_desk()
    if lt_desk is None:
        raise HTTPException(status_code=503, detail="Long-term desk not ready yet")

    ledger  = ExplainabilityLedger.get()
    idea_id = await ledger.submit_user_idea(symbol, body.note[:500])

    # Debate runs in the background — a real Room 1 debate is ~7 sequential
    # LLM calls, too slow to hold the HTTP request open for. The frontend
    # polls GET /ideas and sees status flip pending -> debated when it's
    # done. Deliberately not gated by the long_term_desk feature toggle
    # (which pauses the *scan* loop outside market hours) — a user asking
    # about one specific stock is a cheap, targeted request, not the
    # expensive continuous scan, and the whole point is being able to ask
    # at 8pm and get an answer that night.
    asyncio.create_task(lt_desk.debate_user_idea(idea_id, symbol, body.note[:500]))

    return {"id": idea_id, "symbol": symbol, "status": "pending"}


@router.get("/")
async def list_ideas(limit: int = 50) -> list[dict]:
    ledger = ExplainabilityLedger.get()
    return await ledger.get_user_ideas(limit=min(limit, 200))


@router.post("/{idea_id}/execute")
async def execute_idea(idea_id: int, body: ExecuteIdeaRequest = ExecuteIdeaRequest()) -> dict:
    lt_desk = get_lt_desk()
    if lt_desk is None:
        raise HTTPException(status_code=503, detail="Long-term desk not ready yet")
    try:
        return await lt_desk.execute_user_idea(idea_id, quantity=body.quantity)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
