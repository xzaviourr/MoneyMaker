"""
StrategyMemory — persistent key-value store for strategy learning artifacts.

Stores: parameter histories, regime→performance maps, regime→optimal_params maps.
Backed by SQLite (dev) or PostgreSQL (prod) via JSON serialisation.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import structlog

log = structlog.get_logger(__name__)

_DB_PATH = Path("data/strategy_memory.db")


class StrategyMemory:
    """Thread-safe async memory store for strategy learning."""

    _instance: Optional["StrategyMemory"] = None

    def __init__(self) -> None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        self._lock = asyncio.Lock()
        self._init_schema()

    @classmethod
    def get(cls) -> "StrategyMemory":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _init_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_memory (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy  TEXT NOT NULL,
                key       TEXT NOT NULL,
                value     TEXT NOT NULL,
                regime    TEXT,
                updated   TEXT NOT NULL,
                UNIQUE(strategy, key, regime)
            )
        """)
        self._conn.commit()

    async def store(self, strategy: str, key: str, value: Any,
                    regime: Optional[str] = None) -> None:
        async with self._lock:
            await asyncio.get_event_loop().run_in_executor(
                None,
                self._sync_store,
                strategy, key, value, regime,
            )

    def _sync_store(self, strategy: str, key: str, value: Any,
                    regime: Optional[str]) -> None:
        self._conn.execute("""
            INSERT INTO strategy_memory (strategy, key, value, regime, updated)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(strategy, key, regime) DO UPDATE SET
                value=excluded.value, updated=excluded.updated
        """, (strategy, key, json.dumps(value), regime, datetime.utcnow().isoformat()))
        self._conn.commit()

    async def retrieve(self, strategy: str, key: str,
                       regime: Optional[str] = None) -> Optional[Any]:
        async with self._lock:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                self._sync_retrieve,
                strategy, key, regime,
            )
        return result

    def _sync_retrieve(self, strategy: str, key: str,
                       regime: Optional[str]) -> Optional[Any]:
        row = self._conn.execute("""
            SELECT value FROM strategy_memory
            WHERE strategy=? AND key=? AND (regime=? OR (regime IS NULL AND ? IS NULL))
            ORDER BY updated DESC LIMIT 1
        """, (strategy, key, regime, regime)).fetchone()
        if row:
            try:
                return json.loads(row[0])
            except Exception:
                return row[0]
        return None

    async def get_history(self, strategy: str, key: str,
                          limit: int = 10) -> list[dict]:
        async with self._lock:
            rows = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._conn.execute("""
                    SELECT value, regime, updated FROM strategy_memory
                    WHERE strategy=? AND key=?
                    ORDER BY updated DESC LIMIT ?
                """, (strategy, key, limit)).fetchall()
            )
        return [{"value": json.loads(r[0]), "regime": r[1], "updated": r[2]}
                for r in rows]

    async def all_keys(self, strategy: str) -> list[str]:
        async with self._lock:
            rows = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._conn.execute("""
                    SELECT DISTINCT key FROM strategy_memory WHERE strategy=?
                """, (strategy,)).fetchall()
            )
        return [r[0] for r in rows]
