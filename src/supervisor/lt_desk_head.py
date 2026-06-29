"""
LongTermDeskHead — oversees the deliberative desk,
chairs Room 1 via coordination, approves Room 2 plans.
Reports to FirmCIO.
"""
from __future__ import annotations

import asyncio
import structlog

from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    AllocationPlan,
    IdeaVerdict,
    Message,
    MessageType,
)

log = structlog.get_logger(__name__)


class LongTermDeskHead:
    def __init__(self) -> None:
        self._bus = MessageBus.get()
        self._pending_verdicts: list[IdeaVerdict] = []
        self._pending_plans: list[AllocationPlan] = []
        self._lock = asyncio.Lock()

        self._bus.subscribe(MessageType.IDEA_APPROVED, self._on_idea_approved)
        self._bus.subscribe(MessageType.ALLOCATION_PLAN_READY, self._on_allocation_plan)

    async def _on_idea_approved(self, message: Message) -> None:
        verdict = IdeaVerdict(**message.payload)
        async with self._lock:
            self._pending_verdicts.append(verdict)
        log.info(
            "lt_desk_head.idea_approved",
            idea_id=verdict.idea_id,
            confidence=verdict.confidence_score,
        )

    async def _on_allocation_plan(self, message: Message) -> None:
        """Review allocation plan before forwarding to Room 3."""
        plan = AllocationPlan(**message.payload)
        async with self._lock:
            self._pending_plans.append(plan)
        log.info(
            "lt_desk_head.plan_received",
            idea_id=plan.idea_id,
            buy_symbol=plan.buy_symbol,
            quantity=plan.buy_quantity,
        )
        # Forward to Room 3 execution
        await self._bus.publish(
            Message(
                type=MessageType.ALLOCATION_PLAN_READY,
                source="lt_desk_head",
                payload=plan.model_dump(mode="json"),
            )
        )

    def get_pending_verdicts(self) -> list[IdeaVerdict]:
        return list(self._pending_verdicts)

    def get_pending_plans(self) -> list[AllocationPlan]:
        return list(self._pending_plans)
