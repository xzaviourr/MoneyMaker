"""Portfolio routes — capital snapshot, positions, P&L."""
from __future__ import annotations

import re
from datetime import datetime
from fastapi import APIRouter, HTTPException

from ...brokers.broker_gateway import BrokerGateway
from ...supervisor.capital_tracker import CapitalTracker
from ...foundation.regime_classifier import RegimeClassifier

router = APIRouter()

_SYMBOL_RE   = re.compile(r"^[A-Z0-9&\-]{1,20}$")
_EXCHANGE_RE = re.compile(r"^(NSE|BSE)$")


@router.get("/cache-stats")
async def get_cache_stats() -> dict:
    from ...shared.market_data_cache import cache_stats
    return cache_stats()


@router.get("/status")
async def get_status() -> dict:
    try:
        snap   = await CapitalTracker.get().snapshot()
        regime = RegimeClassifier.get().current
        return {
            "timestamp":    datetime.utcnow().isoformat(),
            "total_capital": float(snap.total_capital),
            "available":     float(snap.available_capital),
            "daily_pnl":     float(snap.daily_pnl),
            "regime":        regime.trend.value,
            "risk_posture":  regime.risk_posture.value,
            "volatility":    regime.volatility.value,
            "bias":          regime.bias.value,
            "vix":           regime.vix,
        }
    except Exception as e:
        return {"timestamp": datetime.utcnow().isoformat(), "regime": "unknown", "error": str(e)}


@router.get("/snapshot")
async def get_capital_snapshot() -> dict:
    try:
        snap = await CapitalTracker.get().snapshot()
        return snap.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/positions")
async def get_positions() -> list[dict]:
    try:
        broker    = BrokerGateway.get()
        positions = await broker.get_positions()
        return [p.model_dump() for p in positions]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trades")
async def get_trades() -> list[dict]:
    """Closed/filled trade book — entry price (for closing sells), exit price,
    realized P&L, and which pod/desk made the trade."""
    try:
        broker = BrokerGateway.get()
        trades = await broker.get_trade_book()
        return list(reversed(trades))  # most recent first
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/balance")
async def get_balance() -> dict:
    try:
        broker  = BrokerGateway.get()
        balance = await broker.get_balance()
        return balance.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/holdings")
async def get_holdings() -> list[dict]:
    """Full position book managed by PortfolioManager — includes entry rationale,
    stop-loss, take-profit, and source for every open holding."""
    from ...portfolio import PortfolioManager
    return PortfolioManager.get().get_holdings()


@router.get("/decisions")
async def get_decisions() -> list[dict]:
    """PM decision log — every HOLD / SELL_ALL / SELL_PARTIAL with reasoning.
    Persists in memory for the lifetime of the backend process (last 200)."""
    from ...portfolio import PortfolioManager
    return PortfolioManager.get().get_decisions()


@router.delete("/positions/{symbol}/{exchange}")
async def purge_bad_position(symbol: str, exchange: str) -> dict:
    if not _SYMBOL_RE.match(symbol):
        raise HTTPException(status_code=422, detail="Invalid symbol format")
    if not _EXCHANGE_RE.match(exchange.upper()):
        raise HTTPException(status_code=422, detail="Exchange must be NSE or BSE")
    """Admin cleanup — void a position that was opened on a fabricated/invalid
    price before validation existed (e.g. a hallucinated symbol). Not a sale;
    refunds the broker cash debited for it. Long-term-desk trades also reserve
    capital in the long_term pillar separately, so that gets released too."""
    broker = BrokerGateway.get()
    pos = await broker.purge_position(symbol, exchange)
    if pos is None:
        raise HTTPException(status_code=404, detail="No such position")

    released_amount = float(pos.average_price * pos.quantity)
    if pos.source_desk == "long_term_desk":
        await CapitalTracker.get().release_lt_desk(pos.symbol, released_amount, pnl=0.0)

    return {"purged": pos.symbol, "refunded_cash": released_amount}
