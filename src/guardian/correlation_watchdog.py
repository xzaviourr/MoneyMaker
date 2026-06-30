"""
CorrelationWatchdog — detects portfolio-wide correlation spikes (crisis convergence).
Early warning before blowup — positions that normally move independently start moving together.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from decimal import Decimal
from typing import Optional

import numpy as np
import structlog

from ..shared.config import toml_cfg
from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    GuardianAlert,
    GuardianResponseMode,
    Message,
    MessageType,
    Position,
    Quote,
)

log = structlog.get_logger(__name__)


class CorrelationWatchdog:
    def __init__(self) -> None:
        cfg = toml_cfg.get("guardian", {})
        self._crisis_threshold = float(cfg.get("correlation_crisis_threshold", 0.85))
        self._window           = 20     # rolling returns window
        self._check_interval   = 60     # seconds
        self._returns: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=self._window))
        self._last_prices: dict[str, Optional[Decimal]] = {}
        self._positions: list[Position] = []
        self._bus  = MessageBus.get()
        self._task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
        self._bus.subscribe(MessageType.QUOTE_UPDATE, self._on_quote)

    def update_positions(self, positions: list[Position]) -> None:
        self._positions = positions

    async def start(self) -> None:
        self._task = asyncio.create_task(self._check_loop(), name="correlation_watchdog")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def _on_quote(self, message: Message) -> None:
        quote = Quote(**message.payload)
        key   = f"{quote.symbol}_{quote.exchange.value}"

        last = self._last_prices.get(key)
        if last and last > 0:
            ret = float((quote.ltp - last) / last)
            self._returns[key].append(ret)
        self._last_prices[key] = quote.ltp

    async def _check_loop(self) -> None:
        while True:
            await asyncio.sleep(self._check_interval)
            try:
                await self._analyse_correlations()
            except Exception as exc:
                log.error("correlation_watchdog.error", error=str(exc))

    async def _analyse_correlations(self) -> None:
        held_keys = [
            f"{p.symbol}_{p.exchange.value}"
            for p in self._positions
        ]
        if len(held_keys) < 3:
            return

        # Build returns matrix for held symbols with enough history
        symbol_returns = {
            k: list(self._returns[k])
            for k in held_keys
            if len(self._returns[k]) >= self._window // 2
        }
        if len(symbol_returns) < 3:
            return

        symbols = list(symbol_returns.keys())
        min_len = min(len(v) for v in symbol_returns.values())
        matrix  = np.array([v[-min_len:] for v in symbol_returns.values()])

        if matrix.shape[1] < 5:
            return

        corr = np.corrcoef(matrix)
        # Average off-diagonal absolute correlation
        n = corr.shape[0]
        off_diag = [
            abs(corr[i, j])
            for i in range(n) for j in range(i + 1, n)
        ]
        avg_corr = float(np.mean(off_diag)) if off_diag else 0.0

        log.debug("correlation_watchdog.avg_corr", avg_correlation=avg_corr)

        if avg_corr >= self._crisis_threshold:
            alert = GuardianAlert(
                mode=GuardianResponseMode.ALERT,
                severity="warning",
                reason=(
                    f"Portfolio correlation spike: avg={avg_corr:.2f} "
                    f"(threshold={self._crisis_threshold}). "
                    f"Crisis convergence detected across {len(symbols)} positions."
                ),
                recommended_action="reduce_position_sizes",
            )
            log.warning("correlation_watchdog.crisis_correlation", avg_corr=avg_corr)
            await self._bus.publish(
                Message(
                    type=MessageType.GUARDIAN_ALERT,
                    source="correlation_watchdog",
                    payload=alert.model_dump(mode="json"),
                )
            )
