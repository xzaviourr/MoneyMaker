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
        self._conn.commit()

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
