"""
ShadowEngine — runs a paper copy of a live pod alongside it with zero capital.

Used for A/B testing parameter changes before promoting to live.
Shadow P&L is compared to live P&L.
Changes are promoted only when shadow outperforms live over 2 weeks.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, TYPE_CHECKING

import structlog

from ..brokers.paper_broker import PaperBroker
from ..brokers.broker_gateway import BrokerGateway
from ..shared.schemas import PodMetrics, Quote

if TYPE_CHECKING:
    from .base_pod import BasePod

log = structlog.get_logger(__name__)


class ShadowEngine:
    """
    Attach to any live pod. Creates a shadow clone running on PaperBroker.
    Tracks comparative performance and recommends promotion.
    """

    def __init__(
        self,
        live_pod: "BasePod",
        shadow_pod: "BasePod",
        promote_after_days: int = 14,
    ) -> None:
        self._live      = live_pod
        self._shadow    = shadow_pod
        self._promote_after = timedelta(days=promote_after_days)
        self._started_at = datetime.utcnow()
        self._lock = asyncio.Lock()

    async def on_quote(self, quote: Quote) -> None:
        """Feed the same quote to both live and shadow pods."""
        # Shadow runs on paper, so just let it generate signals
        try:
            await self._shadow._process_quote(quote)
        except Exception as exc:
            log.error("shadow.process_error", error=str(exc))

    def get_comparison(self) -> dict:
        """Returns live vs shadow P&L comparison."""
        live_m   = self._live.get_metrics()
        shadow_m = self._shadow.get_metrics()

        running_days = (datetime.utcnow() - self._started_at).days

        shadow_better = (
            shadow_m.sharpe_ratio > live_m.sharpe_ratio
            and float(shadow_m.total_pnl) > float(live_m.total_pnl)
        )

        ready_to_promote = (
            shadow_better
            and running_days >= self._promote_after.days
            and shadow_m.total_trades >= 20
        )

        return {
            "running_days": running_days,
            "live_pnl":          str(live_m.total_pnl),
            "shadow_pnl":        str(shadow_m.total_pnl),
            "live_sharpe":       live_m.sharpe_ratio,
            "shadow_sharpe":     shadow_m.sharpe_ratio,
            "live_win_rate":     live_m.win_rate,
            "shadow_win_rate":   shadow_m.win_rate,
            "shadow_better":     shadow_better,
            "ready_to_promote":  ready_to_promote,
        }


class ShadowConflictResolver:
    """Resolves opposing or duplicate signals from shadow vs live."""

    @staticmethod
    def resolve(live_signal, shadow_signal) -> str:
        if live_signal is None and shadow_signal is None:
            return "no_signal"
        if live_signal is None:
            return "use_shadow"
        if shadow_signal is None:
            return "use_live"
        if live_signal.direction == shadow_signal.direction:
            # Same direction → higher conviction wins
            return "use_live" if live_signal.conviction >= shadow_signal.conviction else "use_shadow"
        # Conflicting → don't trade
        return "conflict_skip"
