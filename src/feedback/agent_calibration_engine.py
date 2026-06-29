"""
AgentCalibrationEngine — tracks how well each LT Desk agent predicts outcomes.

For every completed trade that passed through Room 1:
  - Resolves the original votes against actual P&L outcome
  - Updates AgentWeight records
  - Publishes AGENT_WEIGHTS_UPDATED events
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Any

import structlog

from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    AgentWeight,
    Message,
    MessageType,
    TradeAttribution,
)

log = structlog.get_logger(__name__)


class AgentCalibrationEngine:
    """Calibrates conviction accuracy of Room 1 agents over time."""

    def __init__(self) -> None:
        self._weights: dict[str, AgentWeight] = {}
        self._pending: dict[str, dict[str, Any]] = {}   # trade_id → vote record
        self._bus   = MessageBus.get()
        self._lock  = asyncio.Lock()

    async def start(self) -> None:
        self._bus.subscribe(MessageType.IDEA_APPROVED, self._on_idea_approved)
        self._bus.subscribe(MessageType.TRADE_ATTRIBUTED, self._on_attribution)
        log.info("agent_calibration_engine.started")

    async def _on_idea_approved(self, msg: Message) -> None:
        # Keyed by symbol, not idea_id — by the time a trade closes, the
        # attribution event only carries symbol/trade_id (a broker order id,
        # a different namespace entirely from idea_id), so idea_id could
        # never have matched anything here.
        verdict = msg.payload
        symbol = verdict.get("symbol", "")
        if not symbol:
            return
        async with self._lock:
            self._pending[symbol] = {
                "votes":  verdict.get("votes", []),
                "symbol": symbol,
                "ts":     datetime.utcnow(),
            }

    async def _on_attribution(self, msg: Message) -> None:
        data   = msg.payload
        symbol = data.get("symbol", "")
        pnl    = float(data.get("total_pnl", 0))

        # Pods and the Long-Term Desk often watch the same large-cap symbols
        # (TCS, RELIANCE, TATAMOTORS...) independently. Matching on symbol
        # alone meant a fast pod trade closing on a stock could resolve — and
        # wrongly score — an unrelated Long-Term Desk debate's votes on that
        # same symbol. Only resolve votes against a trade that actually came
        # from the Long-Term Desk's own execution.
        if data.get("strategy") != "long_term_idea":
            return

        async with self._lock:
            record = self._pending.pop(symbol, None)
            if not record:
                return

            outcome_positive = pnl > 0
            votes = record.get("votes", [])

            for vote in votes:
                agent_id = vote.get("agent_id", "")
                verdict  = vote.get("verdict", "abstain")
                conf     = float(vote.get("confidence", 0.5))
                weight   = float(vote.get("weight", 1.0))

                if not agent_id or verdict == "abstain":
                    continue

                agent_approved = verdict == "approve"
                correct = (agent_approved == outcome_positive)

                if agent_id not in self._weights:
                    self._weights[agent_id] = AgentWeight(
                        agent_id=agent_id,
                        role=agent_id,
                        current_weight=weight,
                    )

                w = self._weights[agent_id]
                # Increment counters
                w = w.model_copy(update={
                    "total_votes":   w.total_votes + 1,
                    "correct_votes": w.correct_votes + (1 if correct else 0),
                })
                # Bayesian weight update: +5% for correct, -5% for wrong, bounded [0.2, 2.0]
                new_weight = w.current_weight * (1.05 if correct else 0.95)
                new_weight = max(0.2, min(2.0, new_weight))
                w = w.model_copy(update={
                    "current_weight": new_weight,
                    "accuracy": w.correct_votes / w.total_votes,
                    "updated_at": datetime.utcnow(),
                })
                self._weights[agent_id] = w

        await self._bus.publish(Message(
            type=MessageType.AGENT_WEIGHTS_UPDATED,
            payload={k: v.model_dump() for k, v in self._weights.items()},
            source="agent_calibration_engine",
        ))

    def get_weights(self) -> dict[str, AgentWeight]:
        return dict(self._weights)

    def get_agent_weight(self, agent_id: str) -> float:
        w = self._weights.get(agent_id)
        return w.current_weight if w else 1.0
