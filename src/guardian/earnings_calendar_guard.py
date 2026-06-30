"""
EarningsCalendarGuard — tracks upcoming events for every held position.
Flags hold-through-earnings decisions so the trader and Guardian can decide.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional

import structlog

from ..shared.market_data_cache import get_calendar
from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    GuardianAlert,
    GuardianResponseMode,
    Message,
    MessageType,
    Position,
)

log = structlog.get_logger(__name__)

_CHECK_INTERVAL_H = 6   # check every 6 hours
_WARN_DAYS_BEFORE = 2   # flag 2 days before earnings


class EarningsCalendarGuard:
    def __init__(self) -> None:
        self._bus       = MessageBus.get()
        self._positions: list[Position] = []
        self._task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
        self._notified: set[str] = set()  # symbol_event keys already notified

    def update_positions(self, positions: list[Position]) -> None:
        self._positions = positions

    async def start(self) -> None:
        self._task = asyncio.create_task(
            self._check_loop(), name="earnings_calendar_guard"
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def _check_loop(self) -> None:
        while True:
            try:
                await self._check_earnings()
            except Exception as exc:
                log.error("earnings_calendar.error", error=str(exc))
            await asyncio.sleep(_CHECK_INTERVAL_H * 3600)

    async def _check_earnings(self) -> None:
        held = {p.symbol for p in self._positions}
        if not held:
            return

        for symbol in held:
            events = await asyncio.get_event_loop().run_in_executor(
                None, lambda s=symbol: self._fetch_events(s)
            )
            for event_date, event_type in events:
                days_until = (event_date - datetime.utcnow()).days
                if days_until < 0 or days_until > _WARN_DAYS_BEFORE:
                    continue

                alert_key = f"{symbol}_{event_date.date()}_{event_type}"
                if alert_key in self._notified:
                    continue
                self._notified.add(alert_key)

                severity = "warning" if days_until <= 1 else "info"
                alert = GuardianAlert(
                    mode=GuardianResponseMode.ALERT,
                    symbol=symbol,
                    severity=severity,
                    reason=f"{event_type} in {days_until} day(s) on {event_date.date()}",
                    recommended_action="review_hold_through_event",
                )
                log.warning(
                    "earnings_calendar.upcoming_event",
                    symbol=symbol,
                    event_type=event_type,
                    days_until=days_until,
                )
                await self._bus.publish(
                    Message(
                        type=MessageType.GUARDIAN_ALERT,
                        source="earnings_calendar_guard",
                        payload=alert.model_dump(mode="json"),
                    )
                )

    @staticmethod
    def _fetch_events(symbol: str) -> list[tuple[datetime, str]]:
        try:
            # yfinance's .calendar returns a plain dict here (e.g.
            # {"Earnings Date": [date(...)], "Ex-Dividend Date": date(...)}),
            # not a DataFrame — the previous .empty/.columns access was written
            # against the wrong shape and silently returned [] every time,
            # caught by the except below. EarningsCalendarGuard never actually
            # flagged anything as a result.
            cal = get_calendar(symbol)
            if not cal:
                return []
            events: list[tuple[datetime, str]] = []
            for event_type, val in cal.items():
                dates = val if isinstance(val, list) else [val]
                for d in dates:
                    if d is None:
                        continue
                    dt = d if isinstance(d, datetime) else datetime.combine(d, datetime.min.time())
                    events.append((dt, str(event_type)))
            return events
        except Exception:
            return []
