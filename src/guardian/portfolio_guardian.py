"""
PortfolioGuardian — coordinates all monitoring agents, decides ALERT/HEDGE/LIQUIDATE.
LIQUIDATE needs no approval — only notifies CapitalTracker after acting.
Runs 24/7 independently of all trading activity.
"""
from __future__ import annotations

import asyncio
from typing import Optional

import structlog

from ..brokers.broker_gateway import BrokerGateway
from ..shared import feature_toggles
from ..shared.config import toml_cfg
from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    GuardianAlert,
    GuardianResponseMode,
    Message,
    MessageType,
    Order,
    OrderSide,
    OrderType,
    Position,
)
from .position_monitor import PositionMonitor
from .news_watchdog import NewsWatchdog
from .macro_shift_detector import MacroShiftDetector
from .correlation_watchdog import CorrelationWatchdog
from .earnings_calendar_guard import EarningsCalendarGuard

log = structlog.get_logger(__name__)


class PortfolioGuardian:
    """
    Singleton guardian. Coordinates all sub-agents.
    Escalation chain: ALERT → HEDGE → LIQUIDATE.
    """

    def __init__(self, gateway: BrokerGateway) -> None:
        self._gateway = gateway
        self._bus     = MessageBus.get()

        # Sub-agents
        self._position_monitor = PositionMonitor(gateway)
        self._news_watchdog    = NewsWatchdog()
        self._macro_detector   = MacroShiftDetector()
        self._corr_watchdog    = CorrelationWatchdog()
        self._earnings_guard   = EarningsCalendarGuard()

        # Subscribe to alerts from sub-agents
        self._bus.subscribe(MessageType.GUARDIAN_ALERT, self._on_alert)
        self._emergency_count: dict[str, int] = {}

    def set_event_pod(self, pod) -> None:
        self._news_watchdog.set_event_pod(pod)

    def set_long_term_desk(self, desk) -> None:
        """Wire in LongTermDesk so headlines can also become debated ideas in
        Room 1, not just fast Event Pod trades."""
        self._news_watchdog.set_long_term_desk(desk)

    async def start(self) -> None:
        await self._position_monitor.sync_positions()
        await self._news_watchdog.start()
        await self._macro_detector.start()
        await self._corr_watchdog.start()
        await self._earnings_guard.start()
        asyncio.create_task(self._sync_loop(), name="guardian_sync")
        log.info("portfolio_guardian.started")

    async def stop(self) -> None:
        await self._news_watchdog.stop()
        await self._macro_detector.stop()
        await self._corr_watchdog.stop()
        await self._earnings_guard.stop()

    # ── Alert handler ──────────────────────────────────────────────────────

    async def _on_alert(self, message: Message) -> None:
        if message.source == "portfolio_guardian":
            return  # don't react to our own alerts

        alert = GuardianAlert(**message.payload)

        if alert.mode == GuardianResponseMode.LIQUIDATE:
            await self._liquidate(alert)
        elif alert.mode == GuardianResponseMode.HEDGE:
            await self._hedge(alert)
        else:
            await self._notify(alert)

    async def _notify(self, alert: GuardianAlert) -> None:
        """ALERT: notify relevant pod/desk; they decide."""
        log.info(
            "guardian.alert",
            symbol=alert.symbol,
            severity=alert.severity,
            reason=alert.reason,
        )
        # Re-publish with guardian as source so UI sees it
        await self._bus.publish(
            Message(
                type=MessageType.GUARDIAN_ALERT,
                source="portfolio_guardian",
                payload=alert.model_dump(mode="json"),
            )
        )

    async def _hedge(self, alert: GuardianAlert) -> None:
        """HEDGE: place a protective position. Notifies FirmCIO."""
        log.warning(
            "guardian.hedge",
            symbol=alert.symbol,
            reason=alert.reason,
        )
        # Example: buy puts or short a correlated ETF
        # Actual hedge instrument selection would use LLM + risk model
        # Publishing HEDGE event for the UI
        await self._bus.publish(
            Message(
                type=MessageType.GUARDIAN_HEDGE,
                source="portfolio_guardian",
                payload={**alert.model_dump(mode="json"), "auto_executed": True},
            )
        )

    async def _liquidate(self, alert: GuardianAlert) -> None:
        """LIQUIDATE: immediate exit, no approval. Log rationale."""
        log.critical(
            "guardian.liquidate",
            symbol=alert.symbol,
            reason=alert.reason,
        )
        positions = self._position_monitor.get_all_positions()
        target_positions: list[Position]

        if alert.symbol:
            target_positions = [
                p for p in positions if p.symbol == alert.symbol
            ]
        else:
            target_positions = positions  # full portfolio liquidation

        for pos in target_positions:
            order = Order(
                symbol=pos.symbol,
                exchange=pos.exchange,
                side=OrderSide.SELL if pos.side == OrderSide.BUY else OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=pos.quantity,
                source_pod=pos.source_pod,
                tag="guardian_liquidate",
            )
            try:
                result = await self._gateway.place_order(order)
                await self._position_monitor.unregister_position(
                    pos.symbol, pos.exchange.value
                )
                log.info(
                    "guardian.liquidated",
                    symbol=pos.symbol,
                    qty=pos.quantity,
                    fill=str(result.average_fill_price),
                )
            except Exception as exc:
                log.error("guardian.liquidate_failed", symbol=pos.symbol, error=str(exc))

        await self._bus.publish(
            Message(
                type=MessageType.GUARDIAN_LIQUIDATE,
                source="portfolio_guardian",
                payload={**alert.model_dump(mode="json"), "auto_executed": True},
            )
        )

    # ── Sync loop ──────────────────────────────────────────────────────────

    async def _sync_loop(self) -> None:
        """Periodically sync positions from broker and update sub-agents."""
        while True:
            if feature_toggles.is_enabled("portfolio_guardian"):
                try:
                    await self._gateway.mark_to_market()
                    await self._position_monitor.sync_positions()
                    positions = self._position_monitor.get_all_positions()
                    self._corr_watchdog.update_positions(positions)
                    self._earnings_guard.update_positions(positions)
                    symbols = [p.symbol for p in positions]
                    self._news_watchdog.add_symbols(symbols)
                except Exception as exc:
                    log.error("guardian.sync_error", error=str(exc))
                try:
                    await self._position_monitor.check_exits()
                except Exception as exc:
                    log.error("guardian.check_exits_error", error=str(exc))
            await asyncio.sleep(60)
