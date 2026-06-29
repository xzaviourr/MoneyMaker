"""
RegimeClassifier — continuously classifies the market regime.

Outputs: Trend/Choppy/MeanRev, Risk-On/Off, Volatility level, Bull/Bear/Neutral.
Consumed by PodSupervisor, LongTermDesk, CircuitBreaker.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import structlog
import yfinance as yf

from ..shared import feature_toggles
from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    MarketBias,
    MarketRegimeTrend,
    Message,
    MessageType,
    RegimeSnapshot,
    RiskPosture,
    VolatilityLevel,
)

log = structlog.get_logger(__name__)

# Proxy tickers for Indian market regime
_NIFTY50   = "^NSEI"
_INDIAVIX  = "^INDIAVIX"
_USDINR    = "USDINR=X"
_NIFTYMID  = "^NSEMDCP50"

# Classification thresholds
_VOL_LOW_THRESHOLD    = 13.0   # VIX < 13 → low
_VOL_HIGH_THRESHOLD   = 20.0   # VIX > 20 → high
_VOL_CRISIS_THRESHOLD = 30.0   # VIX > 30 → crisis
_ADX_TREND_THRESHOLD  = 25.0   # ADX > 25 → trending
_ADX_CHOP_THRESHOLD   = 15.0   # ADX < 15 → choppy


class RegimeClassifier:
    _instance: "RegimeClassifier | None" = None

    @classmethod
    def get(cls) -> "RegimeClassifier":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._current: RegimeSnapshot = RegimeSnapshot()
        self._bus = MessageBus.get()
        self._task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
        self._update_interval_s = 1800  # re-classify every 30 minutes

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._task = asyncio.create_task(self._classification_loop(), name="regime_classifier")
        log.info("regime_classifier.started",
                 default_regime=self._current.trend.value)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ── Access ─────────────────────────────────────────────────────────────

    @property
    def current(self) -> RegimeSnapshot:
        return self._current

    def is_regime_compatible(self, compatible_regimes: list[MarketRegimeTrend]) -> bool:
        if not compatible_regimes:
            return True
        return self._current.trend in compatible_regimes

    # ── Classification loop ────────────────────────────────────────────────

    async def _classification_loop(self) -> None:
        while True:
            try:
                if feature_toggles.is_enabled("regime_classifier"):
                    await self._classify()
            except Exception as exc:
                log.error("regime_classifier.error", error=str(exc))
            await asyncio.sleep(self._update_interval_s)

    async def _classify(self) -> None:
        snapshot = await asyncio.get_event_loop().run_in_executor(
            None, self._compute_regime
        )
        old_key = self._current.regime_key
        self._current = snapshot

        if snapshot.regime_key != old_key:
            log.info(
                "regime_classifier.regime_change",
                trend=snapshot.trend.value,
                risk=snapshot.risk_posture.value,
                vol=snapshot.volatility.value,
                bias=snapshot.bias.value,
            )
            await self._bus.publish(
                Message(
                    type=MessageType.REGIME_CHANGE,
                    source="regime_classifier",
                    payload=snapshot.model_dump(mode="json"),
                )
            )

    def _compute_regime(self) -> RegimeSnapshot:
        """Blocking — run in executor."""
        try:
            from ..shared.market_data_cache import download as yf_download, is_blocked
            # ── Download data ─────────────────────────────────────────────
            if is_blocked():
                log.debug("regime_classifier.skipped_blocked")
                return self._current

            nifty    = yf_download(_NIFTY50,  period="60d", interval="1d")
            vix_data = yf_download(_INDIAVIX, period="5d",  interval="1d")

            if nifty is None or nifty.empty:
                return self._current  # keep last known if data unavailable

            # yfinance can return MultiIndex columns (e.g. ('Close', '^NSEI')) even
            # for a single ticker — nifty["Close"] is then a 1-column DataFrame, not
            # a Series, and every .iloc[-1]/float() downstream breaks with
            # "must be a string or a real number, not 'Series'". Force a flat Series.
            close = nifty["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]

            vix_close = vix_data["Close"] if not vix_data.empty else None
            if isinstance(vix_close, pd.DataFrame):
                vix_close = vix_close.iloc[:, 0]
            vix = float(vix_close.iloc[-1]) if vix_close is not None and not vix_close.empty else 16.0

            # ── Volatility ───────────────────────────────────────────────
            vol_level = self._classify_volatility(vix)

            # ── Trend (ADX) ──────────────────────────────────────────────
            trend = self._classify_trend(close)

            # ── Market bias (SMA comparison) ─────────────────────────────
            bias = self._classify_bias(close)

            # ── Risk posture (VIX + trend) ────────────────────────────────
            if vol_level in (VolatilityLevel.HIGH, VolatilityLevel.CRISIS) or bias == MarketBias.BEAR:
                risk_posture = RiskPosture.RISK_OFF
            else:
                risk_posture = RiskPosture.RISK_ON

            confidence = self._compute_confidence(close, vix, trend)

            return RegimeSnapshot(
                timestamp=datetime.utcnow(),
                trend=trend,
                risk_posture=risk_posture,
                volatility=vol_level,
                bias=bias,
                vix=vix,
                confidence=confidence,
            )
        except Exception as exc:
            log.error("regime_classifier.compute_error", error=str(exc))
            return self._current

    @staticmethod
    def _classify_volatility(vix: float) -> VolatilityLevel:
        if vix >= _VOL_CRISIS_THRESHOLD:
            return VolatilityLevel.CRISIS
        if vix >= _VOL_HIGH_THRESHOLD:
            return VolatilityLevel.HIGH
        if vix <= _VOL_LOW_THRESHOLD:
            return VolatilityLevel.LOW
        return VolatilityLevel.NORMAL

    @staticmethod
    def _classify_trend(close: pd.Series) -> MarketRegimeTrend:
        """ADX-based trend classification."""
        if len(close) < 14:
            return MarketRegimeTrend.CHOPPY
        high = close.rolling(2).max()
        low  = close.rolling(2).min()

        # Simplified ATR + ADX
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        dm_pos = (high.diff()).clip(lower=0)
        dm_neg = (-low.diff()).clip(lower=0)
        dm_pos = dm_pos.where(dm_pos > dm_neg, 0)
        dm_neg = dm_neg.where(dm_neg > dm_pos, 0)

        di_pos = 100 * dm_pos.rolling(14).mean() / atr.replace(0, np.nan)
        di_neg = 100 * dm_neg.rolling(14).mean() / atr.replace(0, np.nan)
        dx = 100 * ((di_pos - di_neg).abs() / (di_pos + di_neg).replace(0, np.nan))
        adx = dx.rolling(14).mean().iloc[-1]

        if pd.isna(adx):
            return MarketRegimeTrend.CHOPPY
        if adx >= _ADX_TREND_THRESHOLD:
            return MarketRegimeTrend.TRENDING
        if adx <= _ADX_CHOP_THRESHOLD:
            return MarketRegimeTrend.CHOPPY
        return MarketRegimeTrend.MEAN_REVERTING

    @staticmethod
    def _classify_bias(close: pd.Series) -> MarketBias:
        if len(close) < 50:
            return MarketBias.NEUTRAL
        sma20 = close.rolling(20).mean().iloc[-1]
        sma50 = close.rolling(50).mean().iloc[-1]
        ltp   = close.iloc[-1]
        if ltp > sma20 > sma50:
            return MarketBias.BULL
        if ltp < sma20 < sma50:
            return MarketBias.BEAR
        return MarketBias.NEUTRAL

    @staticmethod
    def _compute_confidence(
        close: pd.Series, vix: float, trend: MarketRegimeTrend
    ) -> float:
        """Higher data availability + clear signals → higher confidence."""
        base = 0.5
        if len(close) >= 60:
            base += 0.1
        # Clear VIX extremes → higher confidence
        if vix < 12 or vix > 25:
            base += 0.2
        if trend != MarketRegimeTrend.CHOPPY:
            base += 0.1
        return min(base, 1.0)
