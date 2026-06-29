"""
FlowTracker — records every message passing through the MessageBus.

Used by the /system/graph endpoint to show live data lineage:
which node sent what to whom, and when.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Optional

from .schemas import Message


class FlowTracker:
    """Singleton that subscribes to ALL bus messages and keeps a rolling history."""

    _instance: "FlowTracker | None" = None

    @classmethod
    def get(cls) -> "FlowTracker":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._events: deque[dict] = deque(maxlen=500)
        self._last_by_type:   dict[str, dict] = {}
        self._last_by_source: dict[str, dict] = {}

    # ── Bus handler ───────────────────────────────────────────────────────

    async def handle(self, msg: Message) -> None:
        now    = time.time()
        record = {
            "ts":      now,
            "type":    msg.type.value,
            "source":  msg.source,
            "payload": _summarise(msg.payload),
        }
        self._events.append(record)
        self._last_by_type[msg.type.value]  = record
        self._last_by_source[msg.source]    = record

    # ── Queries ───────────────────────────────────────────────────────────

    def last_of_type(self, type_val: str) -> Optional[dict]:
        return self._last_by_type.get(type_val)

    def last_from(self, source: str) -> Optional[dict]:
        return self._last_by_source.get(source)

    def recent(self, limit: int = 40) -> list[dict]:
        return list(self._events)[-limit:]

    @staticmethod
    def age(ts: float) -> str:
        s = int(time.time() - ts)
        if s < 5:
            return "just now"
        if s < 60:
            return f"{s}s ago"
        return f"{s // 60}m ago"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _summarise(payload: dict | None) -> str:
    """Turn a message payload into a short human-readable string."""
    if not payload:
        return ""
    try:
        # Regime change
        if "trend" in payload and "risk_posture" in payload:
            vix = payload.get("vix", 0)
            return (
                f"{payload['trend']} / {payload['risk_posture']} / "
                f"VIX {float(vix):.1f}"
            )
        # Signal / trade vote
        if "signal" in payload or "direction" in payload:
            sym  = payload.get("symbol", "?")
            side = payload.get("direction") or payload.get("side", "?")
            conf = payload.get("confidence", "")
            conf_str = f" conf={float(conf):.0%}" if conf else ""
            return f"{sym} {side}{conf_str}"
        # Order result
        if "status" in payload and "symbol" in payload:
            return f"{payload['symbol']} {payload.get('status','?')} qty={payload.get('filled_quantity','?')}"
        # Quote
        if "ltp" in payload and "symbol" in payload:
            return f"{payload['symbol']} ₹{float(payload['ltp']):.2f}"
        # Generic: first two k=v pairs
        parts = [f"{k}={v}" for k, v in list(payload.items())[:2]]
        return ", ".join(parts)
    except Exception:
        return str(payload)[:60]
