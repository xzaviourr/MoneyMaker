"""
VoteWeightUpdater — propagates updated AgentWeights back into Room 1 so future
deliberations use calibrated conviction weights.
"""
from __future__ import annotations

import asyncio

import structlog

from ..shared.message_bus import MessageBus
from ..shared.schemas import AgentWeight, Message, MessageType
from .agent_calibration_engine import AgentCalibrationEngine

log = structlog.get_logger(__name__)


class VoteWeightUpdater:
    """Subscribes to AGENT_WEIGHTS_UPDATED and makes weights available to committee agents."""

    def __init__(self, calibration: AgentCalibrationEngine) -> None:
        self._calibration = calibration
        self._weights: dict[str, float] = {}
        self._bus = MessageBus.get()

    async def start(self) -> None:
        self._bus.subscribe(MessageType.AGENT_WEIGHTS_UPDATED, self._on_weights_updated)
        log.info("vote_weight_updater.started")

    async def _on_weights_updated(self, msg: Message) -> None:
        payload = msg.payload
        for agent_id, weight_data in payload.items():
            self._weights[agent_id] = float(weight_data.get("current_weight", 1.0))

        log.debug("vote_weight_updater.refreshed", agents=len(self._weights))

    def get_weight(self, agent_id: str) -> float:
        return self._weights.get(agent_id, 1.0)

    def get_all_weights(self) -> dict[str, float]:
        return dict(self._weights)
