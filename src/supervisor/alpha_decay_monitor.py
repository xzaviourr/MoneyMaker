"""
AlphaDecayMonitor — watches rolling Sharpe per pod and distinguishes
structural alpha decay from temporary bad luck.
Recommends pod demotion to PodSupervisor.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

import numpy as np
import structlog

from ..shared.schemas import PodMetrics, PodState

log = structlog.get_logger(__name__)

_ROLLING_WINDOW_DAYS = 20
_MIN_TRADES_FOR_SIGNAL = 10
_DECAY_SHARPE_THRESHOLD = 0.3   # rolling Sharpe below this → decay signal
_LUCK_SHARPE_THRESHOLD  = -0.5  # single-period Sharpe below this → bad luck


class AlphaDecayMonitor:
    def __init__(self) -> None:
        self._pod_returns: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def record_daily_return(self, pod_id: str, daily_pnl_pct: float) -> None:
        async with self._lock:
            self._pod_returns[pod_id].append((datetime.utcnow(), daily_pnl_pct))
            cutoff = datetime.utcnow() - timedelta(days=_ROLLING_WINDOW_DAYS * 2)
            self._pod_returns[pod_id] = [
                (t, r) for t, r in self._pod_returns[pod_id] if t > cutoff
            ]

    async def analyse(self, pod_id: str) -> dict:
        """
        Returns dict with keys:
          is_decaying: bool
          is_bad_luck: bool
          rolling_sharpe: float
          recommendation: "demote" | "review" | "continue"
        """
        async with self._lock:
            history = self._pod_returns.get(pod_id, [])

        if len(history) < _MIN_TRADES_FOR_SIGNAL:
            return {
                "is_decaying": False,
                "is_bad_luck": False,
                "rolling_sharpe": 0.0,
                "recommendation": "continue",
                "message": f"Insufficient data ({len(history)} days)",
            }

        returns = np.array([r for _, r in history[-_ROLLING_WINDOW_DAYS:]])
        rolling_sharpe = self._compute_sharpe(returns)

        # Structural decay: consistently poor Sharpe
        is_decaying = (
            rolling_sharpe < _DECAY_SHARPE_THRESHOLD
            and len(returns) >= _ROLLING_WINDOW_DAYS
        )

        # Bad luck: extreme negative cluster in recent N days
        recent = returns[-5:] if len(returns) >= 5 else returns
        is_bad_luck = (
            not is_decaying
            and float(np.mean(recent)) < -1.5
            and rolling_sharpe >= _DECAY_SHARPE_THRESHOLD
        )

        if is_decaying:
            recommendation = "demote"
        elif rolling_sharpe < 0.5:
            recommendation = "review"
        else:
            recommendation = "continue"

        result = {
            "is_decaying": is_decaying,
            "is_bad_luck": is_bad_luck,
            "rolling_sharpe": rolling_sharpe,
            "recommendation": recommendation,
            "sample_size": len(returns),
        }
        log.info("alpha_decay.analysed", pod_id=pod_id, **result)
        return result

    @staticmethod
    def _compute_sharpe(returns: np.ndarray, risk_free_daily: float = 0.0) -> float:
        if len(returns) < 2:
            return 0.0
        excess = returns - risk_free_daily
        std = float(np.std(excess, ddof=1))
        if std == 0:
            return 0.0
        return float(np.mean(excess) / std * np.sqrt(252))
