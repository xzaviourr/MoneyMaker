"""
SQLite-backed event log for external service calls (Yahoo Finance, 5Paisa).

Separate from Python's `logging`/structlog (which goes to stdout) — this is
queryable, persisted history shown in the UI Logs page, one row per event.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

_DB_PATH = Path("market_data.db")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS service_logs (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            service   TEXT NOT NULL,
            level     TEXT NOT NULL,
            message   TEXT NOT NULL,
            details   TEXT,
            ts        REAL NOT NULL
        )
    """)
    conn.commit()
    return conn


def log_event(service: str, level: str, message: str, details: Optional[dict[str, Any]] = None) -> None:
    """Record one event for a service ("yahoo_finance" / "five_paisa")."""
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO service_logs (service, level, message, details, ts) VALUES (?, ?, ?, ?, ?)",
            (service, level, message, json.dumps(details, default=str) if details else None, time.time()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_stats() -> dict[str, Any]:
    """Summary of the service_logs table: per-service counts, errors, warnings."""
    try:
        conn = _get_conn()
        total = conn.execute("SELECT COUNT(*) FROM service_logs").fetchone()[0]
        by_service = dict(conn.execute(
            "SELECT service, COUNT(*) FROM service_logs GROUP BY service"
        ).fetchall())
        errors = conn.execute(
            "SELECT COUNT(*) FROM service_logs WHERE level = 'error'"
        ).fetchone()[0]
        warnings = conn.execute(
            "SELECT COUNT(*) FROM service_logs WHERE level = 'warning'"
        ).fetchone()[0]
        conn.close()
        return {
            "total_log_rows": total,
            "by_service":      by_service,
            "errors":          errors,
            "warnings":        warnings,
        }
    except Exception:
        return {"total_log_rows": 0, "by_service": {}, "errors": 0, "warnings": 0}


def get_logs(service: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
    """Return recent log rows, newest first."""
    try:
        conn = _get_conn()
        if service:
            rows = conn.execute(
                "SELECT id, service, level, message, details, ts FROM service_logs "
                "WHERE service = ? ORDER BY ts DESC LIMIT ?",
                (service, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, service, level, message, details, ts FROM service_logs "
                "ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        return [
            {
                "id": r[0],
                "service": r[1],
                "level": r[2],
                "message": r[3],
                "details": json.loads(r[4]) if r[4] else None,
                "ts": r[5],
            }
            for r in rows
        ]
    except Exception:
        return []
