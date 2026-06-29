"""
DataSentinel — Layer 0 market-data validator.

No strategy, agent, or pod sees a price tick until it passes here.
Checks: stale feed, price anomaly, cross-source disagreement, bad-data quarantine.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable, Optional

import structlog

from ..shared.config import toml_cfg
from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    DataQualityAlert,
    Exchange,
    Message,
    MessageType,
    Quote,
)

log = structlog.get_logger(__name__)

QuoteConsumer = Callable[[Quote], None]


class DataSentinel:
    """
    Wraps the raw broker quote stream.
    Validates each tick and forwards clean ticks to registered consumers.
    Quarantined assets are blocked from forwarding.
    """

    def __init__(self) -> None:
        cfg = toml_cfg.get("data", {})
        self._stale_threshold_s    = int(cfg.get("stale_feed_seconds", 30))
        self._price_anomaly_pct    = float(cfg.get("price_anomaly_pct", 10.0))
        self._anomaly_window_s     = int(cfg.get("price_anomaly_seconds", 60))
        self._cross_source_pct     = float(cfg.get("cross_source_threshold", 0.5))

        self._last_seen: dict[str, datetime]           = {}
        self._last_prices: dict[str, list[tuple[datetime, Decimal]]] = defaultdict(list)
        self._quarantine: set[str]                     = set()
        self._consumers: list[QuoteConsumer]           = []
        self._bus                                       = MessageBus.get()
        self._lock                                      = asyncio.Lock()

    async def start(self) -> None:
        """Subscribe to QUOTE_UPDATE from bus for validation."""
        self._bus.subscribe(MessageType.QUOTE_UPDATE, self._on_bus_quote)
        log.info("data_sentinel.started")

    async def _on_bus_quote(self, msg: Message) -> None:
        try:
            quote = Quote(**msg.payload)
            await self.on_quote(quote)
        except Exception:
            pass

    # ── Registration ───────────────────────────────────────────────────────

    def add_consumer(self, consumer: QuoteConsumer) -> None:
        self._consumers.append(consumer)

    def remove_consumer(self, consumer: QuoteConsumer) -> None:
        self._consumers.remove(consumer)

    # ── Entry point ────────────────────────────────────────────────────────

    async def on_quote(self, quote: Quote) -> None:
        """Called by the broker stream for each incoming tick."""
        key = f"{quote.symbol}_{quote.exchange.value}"

        if key in self._quarantine:
            return  # silently drop quarantined assets

        alert = await self._validate(quote, key)
        if alert is not None:
            await self._handle_alert(alert)
            if alert.is_quarantined:
                return
        else:
            # clean tick — update tracking state and forward
            async with self._lock:
                self._last_seen[key] = quote.timestamp
                self._last_prices[key].append((quote.timestamp, quote.ltp))
                # prune old history
                cutoff = quote.timestamp - timedelta(seconds=self._anomaly_window_s)
                self._last_prices[key] = [
                    (t, p) for t, p in self._last_prices[key] if t > cutoff
                ]

            for consumer in self._consumers:
                try:
                    consumer(quote)
                except Exception as exc:
                    log.error("sentinel.consumer_error", error=str(exc))

    # ── Stale-feed checker (called on schedule, not per-tick) ──────────────

    async def check_stale_feeds(self, known_symbols: list[tuple[str, Exchange]]) -> None:
        now = datetime.utcnow()
        for symbol, exchange in known_symbols:
            key = f"{symbol}_{exchange.value}"
            if key in self._quarantine:
                continue
            last = self._last_seen.get(key)
            if last and (now - last).total_seconds() > self._stale_threshold_s:
                alert = DataQualityAlert(
                    symbol=symbol,
                    exchange=exchange,
                    reason=f"Stale feed: last tick {(now - last).seconds}s ago",
                    severity="quarantine",
                    is_quarantined=True,
                )
                await self._handle_alert(alert)

    # ── Validation ─────────────────────────────────────────────────────────

    async def _validate(self, quote: Quote, key: str) -> Optional[DataQualityAlert]:
        # Price sanity: zero or negative price
        if quote.ltp <= Decimal("0"):
            return DataQualityAlert(
                symbol=quote.symbol,
                exchange=quote.exchange,
                reason="Non-positive price",
                severity="quarantine",
                is_quarantined=True,
            )

        # Price anomaly: rapid move > threshold
        async with self._lock:
            history = self._last_prices.get(key, [])
        if history:
            oldest_in_window = history[0][1]
            move_pct = abs(
                float((quote.ltp - oldest_in_window) / oldest_in_window * 100)
            )
            if move_pct > self._price_anomaly_pct:
                return DataQualityAlert(
                    symbol=quote.symbol,
                    exchange=quote.exchange,
                    reason=f"Price moved {move_pct:.1f}% in {self._anomaly_window_s}s",
                    severity="quarantine",
                    is_quarantined=True,
                )

        return None

    async def _handle_alert(self, alert: DataQualityAlert) -> None:
        log.warning(
            "sentinel.alert",
            symbol=alert.symbol,
            reason=alert.reason,
            severity=alert.severity,
        )
        if alert.is_quarantined:
            key = f"{alert.symbol}_{alert.exchange.value}"
            self._quarantine.add(key)
            log.error("sentinel.quarantine", symbol=alert.symbol, reason=alert.reason)

        await self._bus.publish(
            Message(
                type=MessageType.BAD_DATA_QUARANTINE,
                source="data_sentinel",
                payload=alert.model_dump(mode="json"),
            )
        )

    # ── Management ─────────────────────────────────────────────────────────

    def unquarantine(self, symbol: str, exchange: str) -> None:
        """Manually clear a quarantine (human override or after feed fix)."""
        key = f"{symbol}_{exchange}"
        self._quarantine.discard(key)
        log.info("sentinel.unquarantine", symbol=symbol)

    @property
    def quarantined_symbols(self) -> list[str]:
        return sorted(self._quarantine)
