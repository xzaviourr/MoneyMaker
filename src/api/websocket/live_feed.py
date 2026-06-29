"""WebSocket live feed — broadcasts real-time events to UI clients."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...shared.message_bus import MessageBus
from ...shared.schemas import MessageType

log = structlog.get_logger(__name__)
router = APIRouter()

_STREAMED_TYPES = {
    MessageType.QUOTE_UPDATE,
    MessageType.DATA_FETCHED,
    MessageType.REGIME_CHANGE,
    MessageType.POD_SIGNAL,
    MessageType.ORDER_FILLED,
    MessageType.ORDER_PLACED,
    MessageType.GUARDIAN_ALERT,
    MessageType.CIRCUIT_BREAKER_TRIGGERED,
    MessageType.CAPITAL_ALLOCATED,
    MessageType.CAPITAL_RETURNED,
    MessageType.IDEA_APPROVED,
    MessageType.IDEA_REJECTED,
    MessageType.SYSTEM_HEALTH_REPORT,
}


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)
        log.info("ws.client_connected", total=len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)

    async def send(self, ws: WebSocket, data: str) -> None:
        try:
            await ws.send_text(data)
        except Exception:
            self.disconnect(ws)

    async def broadcast(self, data: str) -> None:
        dead: set[WebSocket] = set()
        for ws in list(self._connections):
            try:
                await ws.send_text(data)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self._connections.discard(ws)

    @property
    def count(self) -> int:
        return len(self._connections)


manager = ConnectionManager()
_subscribed = False


def _setup_bus_subscriptions() -> None:
    global _subscribed
    if _subscribed:
        return
    bus = MessageBus.get()
    for msg_type in _STREAMED_TYPES:
        bus.subscribe(msg_type, _relay_to_clients)
    _subscribed = True


async def _relay_to_clients(msg: Any) -> None:
    try:
        ts = getattr(msg, "timestamp", None)
        payload = {
            "type":    msg.type.value if hasattr(msg.type, "value") else str(msg.type),
            "source":  getattr(msg, "source", ""),
            "payload": msg.payload,
            "ts":      ts.isoformat() if hasattr(ts, "isoformat") else str(datetime.utcnow()),
        }
        await manager.broadcast(json.dumps(payload, default=str))
    except Exception:
        pass


async def _heartbeat_loop() -> None:
    """Send system status every 5 seconds so the UI stays connected."""
    while True:
        await asyncio.sleep(5)
        if manager.count == 0:
            continue
        try:
            from ...supervisor.capital_tracker import CapitalTracker
            from ...foundation.regime_classifier import RegimeClassifier
            tracker = CapitalTracker.get()
            snap = await tracker.snapshot()
            regime = RegimeClassifier.get().current
            msg = {
                "type": "heartbeat",
                "source": "system",
                "ts": datetime.utcnow().isoformat(),
                "payload": {
                    "total_capital":  float(snap.total_capital),
                    "available":      float(snap.available_capital),
                    "daily_pnl":      float(snap.daily_pnl),
                    "regime":         regime.trend.value,
                    "risk_posture":   regime.risk_posture.value,
                    "volatility":     regime.volatility.value,
                    "clients":        manager.count,
                },
            }
            await manager.broadcast(json.dumps(msg, default=str))
        except Exception:
            pass


_heartbeat_task: asyncio.Task | None = None


@router.websocket("/ws/live")
async def websocket_endpoint(ws: WebSocket) -> None:
    global _heartbeat_task
    await manager.connect(ws)
    _setup_bus_subscriptions()

    # Start heartbeat loop once
    if _heartbeat_task is None or _heartbeat_task.done():
        _heartbeat_task = asyncio.create_task(_heartbeat_loop())

    # Send welcome message immediately so UI sees "connected"
    welcome = json.dumps({
        "type": "connected",
        "source": "server",
        "ts": datetime.utcnow().isoformat(),
        "payload": {"message": "MoneyMaker connected", "clients": manager.count},
    })
    await manager.send(ws, welcome)

    try:
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await manager.send(ws, json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)
