"""
PortfolioManager — single decision-maker for all open position exits.

Tracks every filled BUY order as a HoldingRecord (with entry price, stop-loss,
take-profit, and the rationale that caused the trade). When new news arrives for
a held stock it calls an LLM to decide: HOLD / SELL_ALL / SELL_PARTIAL. It also
runs a periodic price check and fires the stop-loss / take-profit itself for any
position it is responsible for (primarily long-term / news-approved positions).

Intraday pods manage their own intraday exits (same-day squareoff). This manager
focuses on anything with a multi-hour or multi-day holding horizon.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import structlog

from ..brokers.broker_gateway import BrokerGateway
from ..llm.llm_gateway import LLMGateway
from ..shared.market_data_cache import get_quote
from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    Exchange,
    LLMRequest,
    LLMTier,
    Message,
    MessageType,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)

log = structlog.get_logger(__name__)

_SL_TP_CHECK_INTERVAL = 60  # seconds between price checks for SL/TP

_SYSTEM_PROMPT = """You are a portfolio manager for Indian equities.
A position is being monitored and new market information has just arrived.
Decide whether to hold, exit fully, or trim the position.
Be conservative: only exit on genuinely material negative news or if the
stop-loss / target logic makes sense. Routine noise → HOLD.
Respond ONLY in JSON: {"action": "HOLD" | "SELL_ALL" | "SELL_PARTIAL", "sell_pct": 50, "reasoning": "one or two sentences"}
sell_pct is only relevant for SELL_PARTIAL (ignored otherwise)."""


@dataclass
class HoldingRecord:
    order_id:    str
    symbol:      str
    exchange:    str
    quantity:    int
    entry_price: float
    entry_ts:    str
    stop_loss:   Optional[float]
    take_profit: Optional[float]
    rationale:   str
    source:      str
    strategy:    str


class PortfolioManager:
    _instance: Optional["PortfolioManager"] = None

    def __init__(self) -> None:
        self._bus      = MessageBus.get()
        self._holdings: dict[str, HoldingRecord] = {}  # keyed by order_id
        self._task:    Optional[asyncio.Task] = None    # type: ignore[type-arg]

    @classmethod
    def get(cls) -> "PortfolioManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def start(self) -> None:
        self._bus.subscribe(MessageType.ORDER_FILLED, self._on_order_filled)
        self._bus.subscribe(MessageType.NEWS_SIGNAL,  self._on_news_signal)
        self._task = asyncio.create_task(self._sl_tp_loop())
        log.info("portfolio_manager.started")

    def get_holdings(self) -> list[dict]:
        return [
            {
                "order_id":    h.order_id,
                "symbol":      h.symbol,
                "exchange":    h.exchange,
                "quantity":    h.quantity,
                "entry_price": h.entry_price,
                "entry_ts":    h.entry_ts,
                "stop_loss":   h.stop_loss,
                "take_profit": h.take_profit,
                "rationale":   h.rationale,
                "source":      h.source,
                "strategy":    h.strategy,
            }
            for h in self._holdings.values()
        ]

    # ── Bus handlers ───────────────────────────────────────────────────────────

    async def _on_order_filled(self, msg: Message) -> None:
        payload  = msg.payload or {}
        order    = payload.get("order", {})
        result   = payload.get("result", {})

        if result.get("status") != "filled":
            return

        side   = order.get("side", "")
        symbol = order.get("symbol", "")

        if side == "buy":
            fill_price = result.get("average_fill_price") or order.get("price") or 0
            record = HoldingRecord(
                order_id    = order.get("id", ""),
                symbol      = symbol,
                exchange    = order.get("exchange", "NSE"),
                quantity    = result.get("filled_quantity") or order.get("quantity", 0),
                entry_price = float(fill_price),
                entry_ts    = order.get("created_at", datetime.utcnow().isoformat()),
                stop_loss   = float(order["stop_loss"])   if order.get("stop_loss")   else None,
                take_profit = float(order["take_profit"]) if order.get("take_profit") else None,
                rationale   = order.get("rationale", ""),
                source      = order.get("source_pod") or order.get("source_desk") or "unknown",
                strategy    = order.get("strategy") or order.get("tag") or "unknown",
            )
            self._holdings[record.order_id] = record
            log.info(
                "portfolio_manager.holding_added",
                symbol      = symbol,
                qty         = record.quantity,
                entry_price = record.entry_price,
                stop_loss   = record.stop_loss,
                take_profit = record.take_profit,
                source      = record.source,
                rationale   = record.rationale[:120] if record.rationale else "(none)",
            )

        elif side == "sell":
            removed = [k for k, h in self._holdings.items() if h.symbol == symbol]
            for k in removed:
                del self._holdings[k]
            if removed:
                log.info("portfolio_manager.holding_removed", symbol=symbol, count=len(removed))

    async def _on_news_signal(self, msg: Message) -> None:
        payload = msg.payload or {}
        symbol  = payload.get("symbol", "")
        held    = [h for h in self._holdings.values() if h.symbol == symbol]
        if not held:
            return
        for holding in held:
            asyncio.create_task(self._evaluate_news(holding, payload))

    # ── LLM evaluation ─────────────────────────────────────────────────────────

    async def _evaluate_news(self, holding: HoldingRecord, news: dict) -> None:
        loop = asyncio.get_event_loop()
        current_price = await loop.run_in_executor(
            None, lambda: get_quote(holding.symbol, holding.exchange)
        )
        pnl_pct = 0.0
        if current_price and holding.entry_price:
            pnl_pct = (current_price - holding.entry_price) / holding.entry_price * 100

        user_prompt = (
            f"HELD POSITION: {holding.symbol}\n"
            f"- Shares: {holding.quantity} @ ₹{holding.entry_price:.2f}"
            f" (entered {holding.entry_ts[:10]})\n"
            f"- Entry rationale: \"{holding.rationale or 'not recorded'}\"\n"
            f"- Source / strategy: {holding.source} / {holding.strategy}\n"
            f"- Stop-loss:  {'₹' + f'{holding.stop_loss:.2f}' if holding.stop_loss else 'none'}\n"
            f"- Take-profit: {'₹' + f'{holding.take_profit:.2f}' if holding.take_profit else 'none'}\n"
            f"- Current price: {'₹' + f'{current_price:.2f}' if current_price else 'unavailable'}"
            f" | Unrealised P&L: {pnl_pct:+.2f}%\n\n"
            f"NEW INFORMATION (source: {news.get('source', 'unknown')}):\n"
            f"Headline: {news.get('headline', '')}\n"
            f"Stance: {news.get('stance', '?')} | Severity: {news.get('severity', '?')}"
            f" | Suggested action: {news.get('recommended_action', '?')}\n"
            f"Analyst rationale: {news.get('rationale', '')}\n\n"
            "Should we exit this position? Consider severity, stance, current P&L,"
            " proximity to stop-loss/target."
        )

        try:
            req = LLMRequest(
                agent_id      = "portfolio_manager",
                tier          = LLMTier.FAST,
                system_prompt = _SYSTEM_PROMPT,
                user_prompt   = user_prompt,
                max_tokens    = 256,
                temperature   = 0.0,
                json_mode     = True,
            )
            resp     = await LLMGateway.get().complete(req)
            decision = json.loads(resp.content)
        except Exception as exc:
            log.warning("portfolio_manager.llm_error", error=str(exc))
            return

        action    = (decision.get("action") or "HOLD").upper()
        reasoning = decision.get("reasoning", "")
        sell_pct  = int(decision.get("sell_pct") or 100)

        log.info(
            "portfolio_manager.news_decision",
            symbol    = holding.symbol,
            action    = action,
            sell_pct  = sell_pct if action == "SELL_PARTIAL" else None,
            pnl_pct   = round(pnl_pct, 2),
            reasoning = reasoning,
            headline  = news.get("headline", "")[:100],
        )

        await self._bus.publish(Message(
            type    = MessageType.PORTFOLIO_DECISION,
            source  = "portfolio_manager",
            payload = {
                "symbol":        holding.symbol,
                "action":        action,
                "sell_pct":      sell_pct if action == "SELL_PARTIAL" else (100 if action == "SELL_ALL" else 0),
                "reasoning":     reasoning,
                "trigger":       "news",
                "headline":      news.get("headline", ""),
                "pnl_pct":       round(pnl_pct, 2),
                "current_price": current_price,
            },
        ))

        if action in ("SELL_ALL", "SELL_PARTIAL"):
            qty = holding.quantity if action == "SELL_ALL" else max(1, int(holding.quantity * sell_pct / 100))
            await self._execute_exit(holding, qty, f"news — {reasoning}")

    # ── Periodic SL / TP check ─────────────────────────────────────────────────

    async def _sl_tp_loop(self) -> None:
        while True:
            await asyncio.sleep(_SL_TP_CHECK_INTERVAL)
            for holding in list(self._holdings.values()):
                await self._check_sl_tp(holding)

    async def _check_sl_tp(self, holding: HoldingRecord) -> None:
        loop = asyncio.get_event_loop()
        try:
            price = await loop.run_in_executor(
                None, lambda: get_quote(holding.symbol, holding.exchange)
            )
        except Exception:
            return
        if not price:
            return

        trigger = None
        if holding.stop_loss and price <= holding.stop_loss:
            trigger = f"stop-loss hit: ₹{price:.2f} <= SL ₹{holding.stop_loss:.2f}"
        elif holding.take_profit and price >= holding.take_profit:
            trigger = f"take-profit hit: ₹{price:.2f} >= TP ₹{holding.take_profit:.2f}"

        if not trigger:
            return

        pnl_pct = ((price - holding.entry_price) / holding.entry_price * 100) if holding.entry_price else 0.0
        log.info(
            "portfolio_manager.sl_tp_triggered",
            symbol  = holding.symbol,
            trigger = trigger,
            pnl_pct = round(pnl_pct, 2),
        )
        await self._bus.publish(Message(
            type    = MessageType.PORTFOLIO_DECISION,
            source  = "portfolio_manager",
            payload = {
                "symbol":        holding.symbol,
                "action":        "SELL_ALL",
                "sell_pct":      100,
                "reasoning":     trigger,
                "trigger":       "sl_tp",
                "pnl_pct":       round(pnl_pct, 2),
                "current_price": price,
            },
        ))
        await self._execute_exit(holding, holding.quantity, trigger)

    # ── Order execution ────────────────────────────────────────────────────────

    async def _execute_exit(self, holding: HoldingRecord, quantity: int, reason: str) -> None:
        order = Order(
            symbol     = holding.symbol,
            exchange   = Exchange(holding.exchange),
            side       = OrderSide.SELL,
            order_type = OrderType.MARKET,
            quantity   = quantity,
            source_pod = "portfolio_manager",
            strategy   = "pm_exit",
            rationale  = reason[:200],
            tag        = "pm_exit",
        )
        try:
            result = await BrokerGateway.get().place_order(order)
            if result.status == OrderStatus.FILLED:
                log.info(
                    "portfolio_manager.exit_executed",
                    symbol   = holding.symbol,
                    qty      = quantity,
                    reason   = reason[:120],
                )
            else:
                log.warning(
                    "portfolio_manager.exit_failed",
                    symbol = holding.symbol,
                    status = result.status.value,
                    reason = result.rejection_reason,
                )
        except Exception as exc:
            log.error("portfolio_manager.exit_error", symbol=holding.symbol, error=str(exc))
