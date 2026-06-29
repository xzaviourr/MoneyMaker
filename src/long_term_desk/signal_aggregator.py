"""
SignalAggregator — de-duplicates, cross-validates, and ranks strategy signals
into an IdeaQueue before they enter Room 1 deliberation.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Optional

import structlog

from ..shared.config import toml_cfg
from ..shared.schemas import (
    IdeaQueueItem,
    SignalDirection,
    StrategySignal,
)

log = structlog.get_logger(__name__)


class SignalAggregator:
    def __init__(self) -> None:
        cfg = toml_cfg.get("long_term_desk", {})
        self._min_conviction   = float(cfg.get("min_conviction_to_queue", 0.55))
        self._idea_queue: list[IdeaQueueItem] = []
        self._raw_signals: dict[str, list[StrategySignal]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def ingest(self, signal: StrategySignal) -> None:
        """Ingest one strategy signal."""
        if signal.is_expired:
            return

        key = f"{signal.symbol}_{signal.exchange.value}"
        async with self._lock:
            self._raw_signals[key].append(signal)
            await self._update_idea(key, signal.symbol, signal.exchange)

    async def _update_idea(self, key: str, symbol: str, exchange) -> None:
        signals = [s for s in self._raw_signals[key] if not s.is_expired]
        if not signals:
            return

        longs  = [s for s in signals if s.direction == SignalDirection.LONG]
        shorts = [s for s in signals if s.direction == SignalDirection.SHORT]

        # Net direction and conviction
        if len(longs) >= len(shorts):
            direction   = SignalDirection.LONG
            supporting  = [s.strategy_name for s in longs]
            contra      = [s.strategy_name for s in shorts]
        else:
            direction   = SignalDirection.SHORT
            supporting  = [s.strategy_name for s in shorts]
            contra      = [s.strategy_name for s in longs]

        # Conviction: average of supporting * cross-validation bonus
        avg_conv = sum(s.conviction for s in signals
                       if (direction == SignalDirection.LONG and s.direction == SignalDirection.LONG)
                       or (direction == SignalDirection.SHORT and s.direction == SignalDirection.SHORT)
                      ) / max(len(supporting), 1)

        # Cross-validation bonus: +5% per additional confirming strategy
        cross_bonus = min(0.15, (len(supporting) - 1) * 0.05)
        final_conv  = min(0.95, avg_conv + cross_bonus)

        if final_conv < self._min_conviction:
            return

        # Upsert idea in queue
        existing = next((i for i in self._idea_queue if i.symbol == symbol
                         and i.exchange == exchange), None)
        if existing:
            self._idea_queue.remove(existing)

        from ..shared.config import toml_cfg
        expiry_hours = int(toml_cfg.get("long_term_desk", {}).get("idea_expiry_hours", 48))
        from datetime import timedelta
        item = IdeaQueueItem(
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            conviction_score=final_conv,
            supporting_strategies=supporting,
            contradicting_strategies=contra,
            signals=signals,
            expires_at=datetime.utcnow() + timedelta(hours=expiry_hours),
        )
        # Insert sorted by conviction descending
        self._idea_queue.append(item)
        self._idea_queue.sort(key=lambda x: x.conviction_score, reverse=True)

        log.info(
            "aggregator.idea_queued",
            symbol=symbol,
            direction=direction.value,
            conviction=final_conv,
            strategies=supporting,
        )

    async def pop_idea(self) -> Optional[IdeaQueueItem]:
        """Returns the highest-conviction non-expired idea, removes from queue."""
        async with self._lock:
            self._idea_queue = [i for i in self._idea_queue
                                if not (i.expires_at and datetime.utcnow() > i.expires_at)]
            if not self._idea_queue:
                return None
            return self._idea_queue.pop(0)

    def peek_queue(self) -> list[IdeaQueueItem]:
        return [i for i in self._idea_queue
                if not (i.expires_at and datetime.utcnow() > i.expires_at)]

    def queue_size(self) -> int:
        return len(self.peek_queue())
