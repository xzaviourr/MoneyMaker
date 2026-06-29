"""
LLM usage & cost tracker.
Persists records to DB; exposes daily cost summaries for the UI.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, datetime
from typing import Optional

import structlog

from ..shared.schemas import LLMTier, LLMUsageRecord

log = structlog.get_logger(__name__)


class UsageTracker:
    _instance: Optional["UsageTracker"] = None

    def __init__(self) -> None:
        self._records: list[LLMUsageRecord] = []
        self._lock = asyncio.Lock()
        self._daily_cost: dict[str, float] = defaultdict(float)   # "YYYY-MM-DD" → cost
        self._agent_cost: dict[str, float] = defaultdict(float)   # agent_id → cost today

    @classmethod
    def get(cls) -> "UsageTracker":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def record(self, rec: LLMUsageRecord) -> None:
        async with self._lock:
            self._records.append(rec)
            day = rec.timestamp.strftime("%Y-%m-%d")
            self._daily_cost[day] += rec.cost_usd
            self._agent_cost[rec.agent_id] += rec.cost_usd

    def today_cost(self) -> float:
        today = date.today().strftime("%Y-%m-%d")
        return self._daily_cost.get(today, 0.0)

    def agent_cost_today(self, agent_id: str) -> float:
        return self._agent_cost.get(agent_id, 0.0)

    def cost_by_tier_today(self) -> dict[str, float]:
        today = date.today().strftime("%Y-%m-%d")
        by_tier: dict[str, float] = defaultdict(float)
        for r in self._records:
            if r.timestamp.strftime("%Y-%m-%d") == today:
                by_tier[r.tier.value] += r.cost_usd
        return dict(by_tier)

    def total_tokens_today(self) -> int:
        today = date.today().strftime("%Y-%m-%d")
        return sum(
            r.total_tokens for r in self._records
            if r.timestamp.strftime("%Y-%m-%d") == today
        )
