"""
FlowTracker — records every message passing through the MessageBus.

Used by the /system/graph endpoint to show live data lineage:
which node sent what to whom, and when.

Events are persisted to SQLite (data/flow_tracker.db) so the dashboard
survives server restarts without losing history.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from pathlib import Path
from typing import Optional

import aiosqlite
import structlog

from .schemas import Message

log = structlog.get_logger(__name__)

_DB_PATH = Path("data/flow_tracker.db")
_KEEP_ROWS = 2_000  # rows kept in the DB before trimming


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
        self._lock = asyncio.Lock()
        self._db_ready = False
        # Bootstrap DB in the background; handle() works off the deque until it's ready.
        asyncio.get_event_loop().call_soon(lambda: asyncio.create_task(self._init_db()))

    # ── DB lifecycle ──────────────────────────────────────────────────────

    async def _init_db(self) -> None:
        try:
            _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            async with aiosqlite.connect(_DB_PATH) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS flow_events (
                        id      INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts      REAL    NOT NULL,
                        type    TEXT    NOT NULL,
                        source  TEXT    NOT NULL,
                        payload TEXT    NOT NULL
                    )
                """)
                await db.execute("CREATE INDEX IF NOT EXISTS idx_ts ON flow_events(ts)")
                await db.commit()

                # Load recent events into the in-memory deque on startup
                async with db.execute(
                    "SELECT ts, type, source, payload FROM flow_events ORDER BY ts DESC LIMIT 500"
                ) as cursor:
                    rows = await cursor.fetchall()
                async with self._lock:
                    for ts, typ, src, payload in reversed(rows):
                        record = {"ts": ts, "type": typ, "source": src, "payload": payload}
                        self._events.append(record)
                        self._last_by_type[typ] = record
                        self._last_by_source[src] = record

            self._db_ready = True
            log.info("flow_tracker.db_ready", path=str(_DB_PATH), loaded=len(rows))
        except Exception as exc:
            log.warning("flow_tracker.db_init_failed", error=str(exc))

    async def _persist(self, record: dict) -> None:
        try:
            async with aiosqlite.connect(_DB_PATH) as db:
                await db.execute(
                    "INSERT INTO flow_events (ts, type, source, payload) VALUES (?,?,?,?)",
                    (record["ts"], record["type"], record["source"], record["payload"]),
                )
                # Trim old rows so the DB doesn't grow unbounded
                await db.execute(
                    f"DELETE FROM flow_events WHERE id NOT IN "
                    f"(SELECT id FROM flow_events ORDER BY ts DESC LIMIT {_KEEP_ROWS})"
                )
                await db.commit()
        except Exception as exc:
            log.warning("flow_tracker.persist_failed", error=str(exc))

    # ── Bus handler ───────────────────────────────────────────────────────

    async def handle(self, msg: Message) -> None:
        now    = time.time()
        record = {
            "ts":      now,
            "type":    msg.type.value,
            "source":  msg.source,
            "payload": _summarise(msg.payload),
        }
        async with self._lock:
            self._events.append(record)
            self._last_by_type[msg.type.value]  = record
            self._last_by_source[msg.source]    = record

        if self._db_ready:
            asyncio.create_task(self._persist(record))

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
