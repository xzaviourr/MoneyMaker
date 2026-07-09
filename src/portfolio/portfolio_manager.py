"""
PortfolioManager — single decision-maker for all open position exits.

Tracks every filled BUY order as a HoldingRecord (with entry price, stop-loss,
take-profit, and the rationale that caused the trade). When new news arrives for
a held stock it calls an LLM to decide: HOLD / SELL_ALL / SELL_PARTIAL. It also
runs a periodic price check and fires the stop-loss / take-profit itself.

On startup it hydrates from existing broker positions so it always has a complete
picture of what is open — even across backend restarts.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import structlog

from ..brokers.broker_gateway import BrokerGateway
from ..llm.llm_gateway import LLMGateway
from ..shared.market_data_cache import get_quote
from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    Exchange,
    LLMTier,
    Message,
    MessageType,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)

log = structlog.get_logger(__name__)

_SL_TP_CHECK_INTERVAL = 60   # seconds
_MAX_DECISIONS        = 200  # keep last N decisions in memory

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
        self._bus       = MessageBus.get()
        self._holdings: dict[str, HoldingRecord] = {}   # keyed by position id
        self._decisions: list[dict]              = []   # persistent decision log
        self._task: Optional[asyncio.Task]       = None  # type: ignore[type-arg]

    @classmethod
    def get(cls) -> "PortfolioManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def start(self) -> None:
        self._bus.subscribe(MessageType.ORDER_FILLED, self._on_order_filled)
        self._bus.subscribe(MessageType.NEWS_SIGNAL,  self._on_news_signal)
        asyncio.create_task(self._hydrate_from_broker())  # non-blocking; runs while rest of boot continues
        self._task = asyncio.create_task(self._sl_tp_loop())
        log.info("portfolio_manager.started")

    # ── Public API ─────────────────────────────────────────────────────────────

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

    def get_decisions(self) -> list[dict]:
        """Return decision log, most recent first."""
        return list(reversed(self._decisions))

    # ── Hydration ──────────────────────────────────────────────────────────────

    async def _hydrate_from_broker(self) -> None:
        """Pre-populate holdings from existing broker positions on startup."""
        try:
            broker    = BrokerGateway.get()
            positions = await broker.get_positions()

            # Bulk-fetch rationales for all symbols in one query to avoid
            # 22 sequential lock acquisitions on the ExplainabilityLedger.
            symbols = list({pos.symbol for pos in positions})
            rationale_map: dict[str, str] = {}
            if symbols:
                rationale_map = await self._fetch_rationales_bulk(symbols)

            added = 0
            for pos in positions:
                key = pos.id
                if key in self._holdings:
                    continue
                record = HoldingRecord(
                    order_id    = key,
                    symbol      = pos.symbol,
                    exchange    = pos.exchange.value if hasattr(pos.exchange, "value") else str(pos.exchange),
                    quantity    = int(pos.quantity),
                    entry_price = float(pos.average_price),
                    entry_ts    = pos.opened_at.isoformat() if pos.opened_at else datetime.utcnow().isoformat(),
                    stop_loss   = float(pos.stop_loss)   if pos.stop_loss   else None,
                    take_profit = float(pos.take_profit) if pos.take_profit else None,
                    rationale   = rationale_map.get(pos.symbol, ""),
                    source      = pos.source_pod or pos.source_desk or "unknown",
                    strategy    = pos.strategy or "unknown",
                )
                self._holdings[key] = record
                added += 1
            log.info("portfolio_manager.hydrated", positions=len(positions), added=added)
        except Exception as exc:
            log.warning("portfolio_manager.hydration_failed", error=str(exc))

    async def _fetch_rationales_bulk(self, symbols: list[str]) -> dict[str, str]:
        """Single ledger query for all symbols; avoids per-symbol lock contention on boot."""
        try:
            from ..audit.explainability_ledger import ExplainabilityLedger
            rows = await ExplainabilityLedger.get().query(agent_id="committee_chair", limit=500)
            result: dict[str, str] = {}
            for row in rows:
                sym = row.get("symbol", "")
                if sym not in symbols or sym in result:
                    continue
                outcome = (row.get("outcome") or "").upper()
                if "BOUGHT" in outcome or ("EXECUTED" in outcome and "NOT" not in outcome):
                    result[sym] = row.get("reasoning", "")
            # fill any symbol still missing with the most-recent reasoning
            for row in rows:
                sym = row.get("symbol", "")
                if sym in symbols and sym not in result:
                    result[sym] = row.get("reasoning", "")
            return result
        except Exception:
            return {}

    # ── Bus handlers ───────────────────────────────────────────────────────────

    async def _on_order_filled(self, msg: Message) -> None:
        payload = msg.payload or {}
        order   = payload.get("order", {})
        result  = payload.get("result", {})

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
            f"- Stop-loss:   {'₹' + f'{holding.stop_loss:.2f}'  if holding.stop_loss  else 'none'}\n"
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
            raw      = await LLMGateway.get().complete(
                agent_id      = "portfolio_manager",
                system_prompt = _SYSTEM_PROMPT,
                user_prompt   = user_prompt,
                tier          = LLMTier.FAST,
                max_tokens    = 256,
                temperature   = 0.0,
                json_mode     = True,
            )
            decision = json.loads(raw)
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

        payload = {
            "symbol":        holding.symbol,
            "action":        action,
            "sell_pct":      sell_pct if action == "SELL_PARTIAL" else (100 if action == "SELL_ALL" else 0),
            "reasoning":     reasoning,
            "trigger":       "news",
            "headline":      news.get("headline", ""),
            "pnl_pct":       round(pnl_pct, 2),
            "current_price": current_price,
            "ts":            datetime.utcnow().isoformat(),
            "entry_price":   holding.entry_price,
            "stop_loss":     holding.stop_loss,
            "take_profit":   holding.take_profit,
        }
        self._log_decision(payload)
        await self._bus.publish(Message(
            type    = MessageType.PORTFOLIO_DECISION,
            source  = "portfolio_manager",
            payload = payload,
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

        pnl_pct = ((price - holding.entry_price) / holding.entry_price * 100) if holding.entry_price else 0.0

        trigger = None
        if holding.stop_loss and price <= holding.stop_loss:
            trigger = f"Stop-loss hit — price ₹{price:.2f} ≤ SL ₹{holding.stop_loss:.2f}"
        elif holding.take_profit and price >= holding.take_profit:
            trigger = f"Take-profit hit — price ₹{price:.2f} ≥ TP ₹{holding.take_profit:.2f}"

        if not trigger:
            # Log a monitoring entry so the decision log shows the system is alive
            sl_dist = f"SL ₹{holding.stop_loss:.2f} ({((price - holding.stop_loss) / holding.stop_loss * 100):.1f}% away)" if holding.stop_loss else "no SL"
            tp_dist = f"TP ₹{holding.take_profit:.2f} ({((holding.take_profit - price) / holding.take_profit * 100):.1f}% away)" if holding.take_profit else "no TP"
            self._log_decision({
                "symbol":        holding.symbol,
                "action":        "MONITORING",
                "sell_pct":      0,
                "reasoning":     f"Price ₹{price:.2f} · {sl_dist} · {tp_dist} · No action needed.",
                "trigger":       "sl_tp_check",
                "headline":      None,
                "pnl_pct":       round(pnl_pct, 2),
                "current_price": price,
                "ts":            datetime.utcnow().isoformat(),
                "entry_price":   holding.entry_price,
                "stop_loss":     holding.stop_loss,
                "take_profit":   holding.take_profit,
            })
            return

        log.info(
            "portfolio_manager.sl_tp_triggered",
            symbol  = holding.symbol,
            trigger = trigger,
            pnl_pct = round(pnl_pct, 2),
        )

        payload = {
            "symbol":        holding.symbol,
            "action":        "SELL_ALL",
            "sell_pct":      100,
            "reasoning":     trigger,
            "trigger":       "sl_tp",
            "headline":      None,
            "pnl_pct":       round(pnl_pct, 2),
            "current_price": price,
            "ts":            datetime.utcnow().isoformat(),
            "entry_price":   holding.entry_price,
            "stop_loss":     holding.stop_loss,
            "take_profit":   holding.take_profit,
        }
        self._log_decision(payload)
        await self._bus.publish(Message(
            type    = MessageType.PORTFOLIO_DECISION,
            source  = "portfolio_manager",
            payload = payload,
        ))
        await self._execute_exit(holding, holding.quantity, trigger)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _log_decision(self, payload: dict) -> None:
        self._decisions.append(payload)
        if len(self._decisions) > _MAX_DECISIONS:
            self._decisions.pop(0)

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
                    symbol = holding.symbol,
                    qty    = quantity,
                    reason = reason[:120],
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
