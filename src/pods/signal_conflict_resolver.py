"""
SignalConflictResolver — detects opposing or duplicate signals across pods
on the same asset. Decision: cancel both | higher conviction wins | escalate.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

import structlog

from ..shared.message_bus import MessageBus
from ..shared.schemas import Message, MessageType, SignalDirection, TradeSignal

log = structlog.get_logger(__name__)

_SIGNAL_TTL_SECONDS = 60


class SignalConflictResolver:
    def __init__(self) -> None:
        self._active_signals: dict[str, list[tuple[str, TradeSignal]]] = defaultdict(list)
        self._lock  = asyncio.Lock()
        self._bus   = MessageBus.get()
        self._bus.subscribe(MessageType.POD_SIGNAL, self._on_pod_signal)

    async def _on_pod_signal(self, message: Message) -> None:
        signal = TradeSignal(**message.payload)
        pod_id = message.source
        key    = f"{signal.symbol}_{signal.exchange.value}"

        async with self._lock:
            # Prune stale signals
            now = datetime.utcnow()
            self._active_signals[key] = [
                (p, s) for p, s in self._active_signals[key]
                if (now - s.created_at).seconds < _SIGNAL_TTL_SECONDS
            ]
            self._active_signals[key].append((pod_id, signal))
            signals_for_symbol = self._active_signals[key]

        if len(signals_for_symbol) < 2:
            return

        await self._resolve(key, signals_for_symbol)

    async def _resolve(
        self, symbol_key: str, signals: list[tuple[str, TradeSignal]]
    ) -> None:
        directions = {s.direction for _, s in signals}

        if len(directions) == 1:
            # All same direction — aggregate conviction
            avg_conviction = sum(s.conviction for _, s in signals) / len(signals)
            log.info(
                "conflict_resolver.aligned_signals",
                symbol=symbol_key,
                count=len(signals),
                conviction=avg_conviction,
            )
            return

        # Conflicting directions
        long_signals  = [(p, s) for p, s in signals if s.direction == SignalDirection.LONG]
        short_signals = [(p, s) for p, s in signals if s.direction == SignalDirection.SHORT]

        best_long  = max(long_signals,  key=lambda x: x[1].conviction) if long_signals  else None
        best_short = max(short_signals, key=lambda x: x[1].conviction) if short_signals else None

        if best_long and best_short:
            long_conv  = best_long[1].conviction
            short_conv = best_short[1].conviction
            diff = abs(long_conv - short_conv)

            if diff < 0.15:
                # Too close to call → cancel both
                log.warning(
                    "conflict_resolver.cancel_both",
                    symbol=symbol_key,
                    long_conviction=long_conv,
                    short_conviction=short_conv,
                )
                async with self._lock:
                    self._active_signals[symbol_key] = []
            else:
                # Higher conviction wins
                winner = best_long if long_conv > short_conv else best_short
                log.info(
                    "conflict_resolver.winner",
                    symbol=symbol_key,
                    winner_pod=winner[0],
                    direction=winner[1].direction.value,
                )

    def get_active_signals(self, symbol: str, exchange: str) -> list[TradeSignal]:
        key = f"{symbol}_{exchange}"
        return [s for _, s in self._active_signals.get(key, [])]
