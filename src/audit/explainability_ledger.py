"""
ExplainabilityLedger — immutable audit log of every agent decision with full trace.

Every Room 1/2/3 decision, pod signal, and guardian action is recorded here.
Queryable by symbol, agent, time range.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import structlog

from ..shared.data_paths import DATA_DIR

log = structlog.get_logger(__name__)

_DB_PATH = DATA_DIR / "explainability.db"


class ExplainabilityLedger:
    """Write-once append log for agent decisions."""

    _instance: Optional["ExplainabilityLedger"] = None
    _mode: str = "paper"

    def __init__(self) -> None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        self._lock = asyncio.Lock()
        self._init_schema()

    @classmethod
    def init(cls, mode: str) -> "ExplainabilityLedger":
        cls._mode = mode
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def get(cls) -> "ExplainabilityLedger":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _init_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS decision_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_ts    TEXT    NOT NULL,
                agent_id    TEXT    NOT NULL,
                symbol      TEXT,
                decision    TEXT    NOT NULL,
                reasoning   TEXT,
                inputs      TEXT,
                outputs     TEXT,
                outcome     TEXT,
                mode        TEXT    NOT NULL DEFAULT 'demo'
            )
        """)
        try:
            self._conn.execute("ALTER TABLE decision_log ADD COLUMN mode TEXT NOT NULL DEFAULT 'demo'")
        except sqlite3.OperationalError:
            pass
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_symbol ON decision_log(symbol)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_agent ON decision_log(agent_id)
        """)
        # Tracks what happened to ideas we rejected — the debate pipeline only
        # ever recorded outcomes for what it BOUGHT, so there was no way to
        # tell whether the rejections were good calls or missed opportunity.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS rejected_idea_tracking (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol           TEXT    NOT NULL,
                rejected_at      TEXT    NOT NULL,
                rejection_price  REAL    NOT NULL,
                rejection_reason TEXT,
                room             TEXT,
                last_checked_at  TEXT,
                last_price       REAL,
                pct_change       REAL,
                still_tracking   INTEGER NOT NULL DEFAULT 1
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rejected_tracking_active
            ON rejected_idea_tracking(still_tracking)
        """)
        self._conn.commit()

    async def record_rejection(
        self, symbol: str, rejection_price: float, rejection_reason: str, room: str,
    ) -> None:
        """Called every time an idea is rejected (Room 2 or Room 3), so its
        actual outcome can be checked later — see rejected_idea_tracker.py."""
        async with self._lock:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: (
                    self._conn.execute("""
                        INSERT INTO rejected_idea_tracking
                            (symbol, rejected_at, rejection_price, rejection_reason, room)
                        VALUES (?, ?, ?, ?, ?)
                    """, (symbol, datetime.utcnow().isoformat(), rejection_price, rejection_reason, room)),
                    self._conn.commit(),
                )
            )

    async def get_active_rejections(self) -> list[dict[str, Any]]:
        """Rejections still within their tracking window, for the daily price-check job."""
        async with self._lock:
            rows = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._conn.execute("""
                    SELECT id, symbol, rejected_at, rejection_price
                    FROM rejected_idea_tracking WHERE still_tracking=1
                """).fetchall()
            )
        return [{"id": r[0], "symbol": r[1], "rejected_at": r[2], "rejection_price": r[3]} for r in rows]

    async def update_rejection_price(self, row_id: int, price: float, still_tracking: bool) -> None:
        async with self._lock:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: (
                    self._conn.execute("""
                        UPDATE rejected_idea_tracking
                        SET last_checked_at=?, last_price=?,
                            pct_change=(? - rejection_price) / rejection_price * 100,
                            still_tracking=?
                        WHERE id=?
                    """, (datetime.utcnow().isoformat(), price, price, 1 if still_tracking else 0, row_id)),
                    self._conn.commit(),
                )
            )

    async def query_rejected_tracking(self, limit: int = 200) -> list[dict[str, Any]]:
        async with self._lock:
            rows = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._conn.execute("""
                    SELECT symbol, rejected_at, rejection_price, rejection_reason, room,
                           last_checked_at, last_price, pct_change, still_tracking
                    FROM rejected_idea_tracking
                    WHERE last_price IS NOT NULL
                    ORDER BY rejected_at DESC LIMIT ?
                """, (limit,)).fetchall()
            )
        cols = ["symbol", "rejected_at", "rejection_price", "rejection_reason", "room",
                "last_checked_at", "last_price", "pct_change", "still_tracking"]
        return [dict(zip(cols, r)) for r in rows]

    async def record(
        self,
        agent_id:  str,
        decision:  str,
        reasoning: str = "",
        inputs:    Optional[dict[str, Any]] = None,
        outputs:   Optional[dict[str, Any]] = None,
        symbol:    Optional[str] = None,
    ) -> None:
        async with self._lock:
            await asyncio.get_event_loop().run_in_executor(
                None,
                self._sync_record,
                agent_id, decision, reasoning, inputs, outputs, symbol,
            )

    def _sync_record(self, agent_id: str, decision: str, reasoning: str,
                     inputs: Optional[dict], outputs: Optional[dict],
                     symbol: Optional[str]) -> None:
        self._conn.execute("""
            INSERT INTO decision_log
                (event_ts, agent_id, symbol, decision, reasoning, inputs, outputs, mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.utcnow().isoformat(),
            agent_id,
            symbol,
            decision,
            reasoning,
            json.dumps(inputs or {}),
            json.dumps(outputs or {}),
            ExplainabilityLedger._mode,
        ))
        self._conn.commit()

    async def update_outcome(self, symbol: str, agent_id: str, outcome: str) -> None:
        """Retrospectively attach outcome to the most recent decision for this symbol/agent."""
        async with self._lock:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._conn.execute("""
                    UPDATE decision_log SET outcome=?
                    WHERE id=(
                        SELECT id FROM decision_log
                        WHERE symbol=? AND agent_id=?
                        ORDER BY event_ts DESC LIMIT 1
                    )
                """, (outcome, symbol, agent_id)) and self._conn.commit()
            )

    async def query(
        self,
        symbol:   Optional[str] = None,
        agent_id: Optional[str] = None,
        mode:     Optional[str] = None,
        limit:    int = 50,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            rows = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._query_sync(symbol, agent_id, mode, limit)
            )
        return rows

    def _query_sync(self, symbol: Optional[str], agent_id: Optional[str],
                    mode: Optional[str], limit: int) -> list[dict[str, Any]]:
        conditions = []
        params: list[Any] = []
        if symbol:
            conditions.append("symbol=?")
            params.append(symbol)
        if agent_id:
            conditions.append("agent_id LIKE ?")
            params.append(f"%{agent_id}%")
        if mode:
            conditions.append("mode=?")
            params.append(mode)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)
        rows = self._conn.execute(f"""
            SELECT event_ts, agent_id, symbol, decision, reasoning, inputs, outputs, outcome, mode
            FROM decision_log {where}
            ORDER BY event_ts DESC LIMIT ?
        """, params).fetchall()
        cols = ["event_ts", "agent_id", "symbol", "decision",
                "reasoning", "inputs", "outputs", "outcome", "mode"]
        return [dict(zip(cols, r)) for r in rows]
