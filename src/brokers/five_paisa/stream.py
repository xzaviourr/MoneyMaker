"""
5Paisa WebSocket live quote stream wrapper.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Callable, Optional

import structlog

from ...shared.schemas import Exchange, Quote
from .symbol_mapper import SymbolMapper

log = structlog.get_logger(__name__)

QuoteCallback = Callable[[Quote], None]


class FivePaisaStream:
    def __init__(self, client) -> None:  # type: ignore[type-arg]
        self._client = client
        self._callback: Optional[QuoteCallback] = None
        self._subscribed: list[tuple[str, str]] = []
        self._task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
        self._mapper = SymbolMapper.get()

    async def subscribe(
        self, symbols: list[tuple[str, str]], callback: QuoteCallback
    ) -> None:
        self._subscribed = symbols
        self._callback = callback
        await self._mapper.ensure_loaded()

        req = []
        for symbol, exchange in symbols:
            code = self._mapper.get_scrip_code(symbol, exchange)
            if code:
                req.append({
                    "Exch": self._mapper.exchange_code(exchange),
                    "ExchType": "C",
                    "ScripCode": code,
                })

        if not req:
            log.warning("stream.no_valid_symbols")
            return

        def on_data(data):  # type: ignore[type-arg]
            asyncio.create_task(self._process_tick(data))

        # py5paisa WebSocket subscription
        self._task = asyncio.create_task(
            asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.subscribe_feed(req, on_data),
            )
        )
        log.info("stream.subscribed", count=len(req))

    async def _process_tick(self, raw: dict) -> None:
        try:
            from datetime import datetime
            code = int(raw.get("Token", 0))
            symbol = self._mapper.get_symbol(code) or str(code)
            price = Decimal(str(raw.get("LastRate", 0)))
            quote = Quote(
                symbol=symbol,
                exchange=Exchange.NSE,
                timestamp=datetime.utcnow(),
                ltp=price,
                open=Decimal(str(raw.get("OpenRate", price))),
                high=Decimal(str(raw.get("High", price))),
                low=Decimal(str(raw.get("Low", price))),
                close=Decimal(str(raw.get("CloseRate", price))),
                volume=int(raw.get("TotalQty", 0)),
                bid=Decimal(str(raw.get("BidRate", 0))) or None,
                ask=Decimal(str(raw.get("OffRate", 0))) or None,
            )
            if self._callback:
                self._callback(quote)
        except Exception as exc:
            log.error("stream.tick_error", error=str(exc))

    async def unsubscribe(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._client.unsubscribe()
            )
        except Exception:
            pass
        log.info("stream.unsubscribed")
