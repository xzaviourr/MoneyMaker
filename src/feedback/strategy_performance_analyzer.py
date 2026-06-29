"""
StrategyPerformanceAnalyzer — tracks per-strategy win rate, expectancy, Sharpe, regime fit.
Runs regime-adjusted scoring so a strategy isn't penalized for trading in its off-regime.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import structlog

from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    MarketRegimeTrend,
    Message,
    MessageType,
    TradeAttribution,
)

log = structlog.get_logger(__name__)


class StrategyRecord:
    def __init__(self) -> None:
        self.trades:     list[dict] = []
        self.pnl_series: list[float] = []

    @property
    def total(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t["pnl"] > 0)

    @property
    def win_rate(self) -> float:
        return self.wins / self.total if self.total > 0 else 0.0

    @property
    def avg_win(self) -> float:
        wins = [t["pnl"] for t in self.trades if t["pnl"] > 0]
        return float(np.mean(wins)) if wins else 0.0

    @property
    def avg_loss(self) -> float:
        losses = [abs(t["pnl"]) for t in self.trades if t["pnl"] < 0]
        return float(np.mean(losses)) if losses else 0.0

    @property
    def expectancy(self) -> float:
        if self.avg_loss == 0:
            return self.avg_win * self.win_rate
        return self.win_rate * self.avg_win - (1 - self.win_rate) * self.avg_loss

    @property
    def sharpe(self) -> float:
        if len(self.pnl_series) < 10:
            return 0.0
        arr = np.array(self.pnl_series)
        std = float(np.std(arr))
        return float(np.mean(arr)) / std * np.sqrt(252) if std > 0 else 0.0


class StrategyPerformanceAnalyzer:
    def __init__(self) -> None:
        self._records: dict[str, StrategyRecord] = defaultdict(StrategyRecord)
        self._regime_records: dict[tuple, StrategyRecord] = defaultdict(StrategyRecord)
        self._bus = MessageBus.get()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._bus.subscribe(MessageType.TRADE_ATTRIBUTED, self._on_attribution)
        log.info("strategy_performance_analyzer.started")

    async def _on_attribution(self, msg: Message) -> None:
        try:
            data   = msg.payload
            strat  = data.get("strategy") or "unknown"
            pnl    = float(data.get("total_pnl", 0))
            regime = data.get("regime_at_entry", {})
            trend  = regime.get("trend", "") if regime else ""

            async with self._lock:
                rec = self._records[strat]
                rec.trades.append({"pnl": pnl, "ts": datetime.utcnow()})
                rec.pnl_series.append(pnl)

                if trend:
                    rkey = (strat, trend)
                    self._regime_records[rkey].trades.append({"pnl": pnl})
                    self._regime_records[rkey].pnl_series.append(pnl)
        except Exception:
            log.exception("strategy_analyzer.on_attribution_error")

    def get_stats(self, strategy: str) -> dict:
        rec = self._records.get(strategy)
        if not rec or rec.total == 0:
            return {}
        return {
            "strategy":   strategy,
            "total":      rec.total,
            "win_rate":   rec.win_rate,
            "expectancy": rec.expectancy,
            "sharpe":     rec.sharpe,
            "avg_win":    rec.avg_win,
            "avg_loss":   rec.avg_loss,
        }

    def get_regime_stats(self, strategy: str, trend: str) -> dict:
        rec = self._regime_records.get((strategy, trend))
        if not rec or rec.total == 0:
            return {}
        return {
            "strategy":  strategy,
            "regime":    trend,
            "total":     rec.total,
            "win_rate":  rec.win_rate,
            "sharpe":    rec.sharpe,
            "expectancy": rec.expectancy,
        }

    def all_stats(self) -> list[dict]:
        return [self.get_stats(s) for s in self._records]

    def weakest_strategies(self, n: int = 3) -> list[str]:
        ranked = sorted(
            self._records.items(),
            key=lambda kv: kv[1].sharpe
        )
        return [k for k, _ in ranked[:n]]
