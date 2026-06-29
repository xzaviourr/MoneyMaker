"""
CrowdingDetector — measures cross-pod holdings overlap before trades fire.
Prevents unintended concentration by detecting when multiple pods hold
correlated or identical positions.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from decimal import Decimal
from typing import Optional

import structlog

from ..shared.schemas import Position, TradeSignal

log = structlog.get_logger(__name__)

_DEFAULT_CONCENTRATION_THRESHOLD = 0.25  # >25% of total capital in one symbol = crowded
_DEFAULT_CROSS_POD_OVERLAP       = 3     # >3 pods holding same symbol = crowded


class CrowdingDetector:
    def __init__(
        self,
        concentration_threshold: float = _DEFAULT_CONCENTRATION_THRESHOLD,
        max_cross_pod_overlap: int = _DEFAULT_CROSS_POD_OVERLAP,
    ) -> None:
        self._conc_threshold   = concentration_threshold
        self._max_overlap      = max_cross_pod_overlap
        self._positions: dict[str, list[Position]] = defaultdict(list)   # pod_id → positions
        self._total_capital    = Decimal("1000000")
        self._lock             = asyncio.Lock()

    async def update_positions(self, pod_id: str, positions: list[Position]) -> None:
        async with self._lock:
            self._positions[pod_id] = positions

    async def set_total_capital(self, total: Decimal) -> None:
        async with self._lock:
            self._total_capital = total

    async def check_signal(self, signal: TradeSignal) -> dict:
        """
        Returns:
          allowed: bool
          reason: str
          concentration_pct: float
          pods_with_same_symbol: int
        """
        async with self._lock:
            # Count pods already holding this symbol
            pods_with_symbol = [
                pod_id
                for pod_id, positions in self._positions.items()
                for p in positions
                if p.symbol == signal.symbol
            ]

            # Total exposure to this symbol
            total_exposure = sum(
                p.market_value
                for positions in self._positions.values()
                for p in positions
                if p.symbol == signal.symbol
            )

            concentration_pct = float(
                total_exposure / self._total_capital * 100
            ) if self._total_capital > 0 else 0.0

        is_crowded_by_overlap = len(pods_with_symbol) >= self._max_overlap
        is_crowded_by_conc    = concentration_pct / 100 >= self._conc_threshold

        allowed = not (is_crowded_by_overlap or is_crowded_by_conc)
        reason  = ""
        if is_crowded_by_conc:
            reason = f"Symbol concentration {concentration_pct:.1f}% exceeds {self._conc_threshold*100:.0f}% limit"
        elif is_crowded_by_overlap:
            reason = f"{len(pods_with_symbol)} pods already hold {signal.symbol}"

        if not allowed:
            log.warning(
                "crowding.blocked",
                symbol=signal.symbol,
                reason=reason,
                pods_count=len(pods_with_symbol),
                concentration_pct=concentration_pct,
            )

        return {
            "allowed": allowed,
            "reason": reason,
            "concentration_pct": concentration_pct,
            "pods_with_same_symbol": len(pods_with_symbol),
        }

    async def get_crowding_report(self) -> dict:
        """Returns a summary of current cross-pod holdings overlap."""
        async with self._lock:
            symbol_pods: dict[str, list[str]] = defaultdict(list)
            for pod_id, positions in self._positions.items():
                for p in positions:
                    symbol_pods[p.symbol].append(pod_id)

        crowded = {
            sym: pods for sym, pods in symbol_pods.items() if len(pods) >= self._max_overlap
        }
        return {
            "crowded_symbols": crowded,
            "total_unique_symbols": len(symbol_pods),
        }
