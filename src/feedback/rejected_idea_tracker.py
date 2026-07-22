"""
RejectedIdeaTracker — daily check on what happened to ideas we rejected.

The debate pipeline only ever recorded outcomes for what it bought — there
was no way to tell whether a rejection (risk gate, "wait for a better
entry", too correlated) was actually a good call or a missed opportunity.
This checks each still-tracked rejection's real price once a day and
records the % move since rejection, for up to _TRACKING_DAYS.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import structlog

from ..audit.explainability_ledger import ExplainabilityLedger
from ..shared import market_data_cache

log = structlog.get_logger(__name__)

_CHECK_INTERVAL_SECONDS = 24 * 3600  # once a day
_TRACKING_DAYS = 180


class RejectedIdeaTracker:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="rejected_idea_tracker")
        log.info("rejected_idea_tracker.started")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                await self._check_all()
            except Exception:
                log.exception("rejected_idea_tracker.check_failed")
            await asyncio.sleep(_CHECK_INTERVAL_SECONDS)

    async def _check_all(self) -> None:
        ledger = ExplainabilityLedger.get()
        rows = await ledger.get_active_rejections()
        if not rows:
            return

        loop = asyncio.get_event_loop()
        checked = 0
        for row in rows:
            age_days = (datetime.utcnow() - datetime.fromisoformat(row["rejected_at"])).days
            still_tracking = age_days < _TRACKING_DAYS
            price = await loop.run_in_executor(
                None, lambda s=row["symbol"]: market_data_cache.get_quote(s, "NSE")
            )
            if price is None:
                continue
            await ledger.update_rejection_price(row["id"], price, still_tracking)
            checked += 1

        log.info("rejected_idea_tracker.checked", total=len(rows), updated=checked)
