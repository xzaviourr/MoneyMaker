"""
RegimeAdjustedScorer — normalises strategy performance for regime difficulty.

A strategy that loses 0.5% in a choppy sideways regime may be outperforming
a strategy that gains 1% in a strong bull trend. This scorer adjusts for that.
"""
from __future__ import annotations

from datetime import datetime

import structlog

from ..foundation.regime_classifier import RegimeClassifier
from ..shared.schemas import MarketRegimeTrend
from .strategy_performance_analyzer import StrategyPerformanceAnalyzer

log = structlog.get_logger(__name__)

# Baseline expected daily P&L% by regime (empirical priors)
_REGIME_BASELINE = {
    MarketRegimeTrend.TRENDING:      0.0015,   # 0.15% per trade expected in strong trend
    MarketRegimeTrend.MEAN_REVERTING: 0.0008,
    MarketRegimeTrend.CHOPPY:        -0.0003,  # expect to lose in choppy
}


class RegimeAdjustedScorer:
    def __init__(self, analyzer: StrategyPerformanceAnalyzer) -> None:
        self._analyzer  = analyzer
        self._classifier = RegimeClassifier.get() if hasattr(RegimeClassifier, "get") else None

    def adjusted_score(self, strategy: str, current_regime: MarketRegimeTrend) -> float:
        """Returns regime-adjusted Sharpe (higher = better than regime baseline)."""
        stats = self._analyzer.get_stats(strategy)
        if not stats or stats.get("total", 0) < 10:
            return 0.0

        raw_sharpe = stats.get("sharpe", 0.0)
        # Regime scores give context on how hard the environment is
        regime_stats = self._analyzer.get_regime_stats(strategy, current_regime.value)
        regime_sharpe = regime_stats.get("sharpe", raw_sharpe)

        baseline = _REGIME_BASELINE.get(current_regime, 0.0)
        # Adjust: subtract the "free" return available in this regime
        adjusted = regime_sharpe - baseline * 252  # annualise baseline
        return adjusted

    def rank_strategies(self, current_regime: MarketRegimeTrend) -> list[tuple[str, float]]:
        """Returns strategies sorted by regime-adjusted score (best first)."""
        records = self._analyzer.all_stats()
        ranked  = []
        for s in records:
            name  = s.get("strategy", "")
            score = self.adjusted_score(name, current_regime)
            ranked.append((name, score))
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked

    def should_disable_in_regime(self, strategy: str,
                                  current_regime: MarketRegimeTrend) -> bool:
        """Returns True if strategy consistently loses in this regime."""
        regime_stats = self._analyzer.get_regime_stats(strategy, current_regime.value)
        if not regime_stats or regime_stats.get("total", 0) < 10:
            return False
        return regime_stats.get("expectancy", 0) < -0.5  # losing on average
