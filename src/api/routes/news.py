"""News route — pending "buy this?" approvals raised by the News Extractor.

Nothing here ever trades on its own. A "buy" gist only becomes a real paper
order if the user explicitly hits Approve; rejecting (or ignoring) it does
nothing.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import structlog
from fastapi import APIRouter, HTTPException

from .system import get_guardian
from ...brokers.broker_gateway import BrokerGateway
from ...shared.market_data_cache import get_info, get_quote
from ...shared.schemas import Exchange, Order, OrderSide, OrderStatus, OrderType
from ...supervisor.capital_tracker import CapitalTracker
from ...supervisor.circuit_breaker import CircuitBreaker

log = structlog.get_logger(__name__)
router = APIRouter()

_POSITION_PCT = 0.02  # same "max 2% per trade" rule used elsewhere in the system


def _watchdog():
    guardian = get_guardian()
    if guardian is None:
        raise HTTPException(status_code=503, detail="Guardian not ready yet")
    return guardian._news_watchdog


@router.get("/pending")
async def get_pending() -> list[dict]:
    return _watchdog().get_pending_approvals()


@router.get("/detail/{approval_id}")
async def get_detail(approval_id: str) -> dict:
    """Read-only preview — current price, the stop-loss/target levels and
    share count Approve would actually use, plus company context and the
    original source link, so the user can judge a suggestion before acting
    on it. Does not resolve or remove the approval."""
    item = _watchdog().get_approval(approval_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Approval not found or already resolved")

    symbol = item["symbol"]
    loop = asyncio.get_event_loop()
    price = await loop.run_in_executor(None, lambda: get_quote(symbol))
    info  = await loop.run_in_executor(None, lambda: get_info(symbol))
    info  = info or {}

    detail = {
        **item,
        "current_price": price,
        "stop_loss_price":   round(price * 0.95, 2) if price else None,
        "take_profit_price": round(price * 1.10, 2) if price else None,
        "sector":            info.get("sector"),
        "industry":          info.get("industry"),
        "market_cap":        info.get("marketCap"),
        "fifty_two_week_low":  info.get("fiftyTwoWeekLow"),
        "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
    }

    if price:
        available = await CapitalTracker.get().available_in_pillar("long_term")
        budget    = float(available) * _POSITION_PCT
        detail["estimated_quantity"] = int(budget // price)
        detail["estimated_cost"]     = round(int(budget // price) * price, 2)
    else:
        detail["estimated_quantity"] = 0
        detail["estimated_cost"]     = 0

    return detail


@router.post("/approve/{approval_id}")
async def approve(approval_id: str) -> dict:
    item = _watchdog().pop_approval(approval_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Approval not found or already resolved")

    if CircuitBreaker.get().is_halted():
        return {"status": "blocked", "reason": "circuit breaker is halted — no new trades right now"}

    symbol = item["symbol"]
    loop = asyncio.get_event_loop()
    price = await loop.run_in_executor(None, lambda: get_quote(symbol))
    if not price:
        return {"status": "blocked", "reason": f"no live price available for {symbol}"}

    tracker   = CapitalTracker.get()
    available = await tracker.available_in_pillar("long_term")
    budget    = float(available) * _POSITION_PCT
    quantity  = int(budget // price)
    if quantity < 1:
        return {"status": "blocked", "reason": "not enough available capital for even 1 share"}

    try:
        await tracker.reserve_for_lt_desk(symbol, quantity * price)
    except Exception as exc:
        return {"status": "blocked", "reason": f"capital reservation failed: {exc}"}

    order = Order(
        symbol=symbol,
        exchange=Exchange.NSE,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=quantity,
        stop_loss=Decimal(str(round(price * 0.95, 2))),
        take_profit=Decimal(str(round(price * 1.10, 2))),
        source_pod="news_watchdog",
        strategy="news_buy_suggestion",
        tag="user_approved_news",
        rationale=item.get("rationale", ""),
    )
    result = await BrokerGateway.get().place_order(order)

    if result.status == OrderStatus.REJECTED:
        await tracker.release_lt_desk(symbol, quantity * price)
        return {"status": "rejected", "reason": result.rejection_reason}

    log.info("news.approval_executed", symbol=symbol, quantity=quantity, order_id=order.id)
    return {"status": "placed", "symbol": symbol, "quantity": quantity, "order_id": order.id}


@router.post("/reject/{approval_id}")
async def reject(approval_id: str) -> dict:
    item = _watchdog().pop_approval(approval_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Approval not found or already resolved")
    return {"status": "rejected", "symbol": item["symbol"]}
