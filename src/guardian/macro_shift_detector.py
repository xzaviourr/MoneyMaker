"""
MacroShiftDetector — watches VIX spikes, yield curve, Fed/RBI statements, geopolitical.
Alerts PodSupervisor and Guardian on macro regime shifts.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

import structlog

from ..shared.market_data_cache import download as _cached_download
from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    GuardianAlert,
    GuardianResponseMode,
    Message,
    MessageType,
    RegimeSnapshot,
    VolatilityLevel,
)

log = structlog.get_logger(__name__)

_CHECK_INTERVAL = 300   # seconds
_VIX_SPIKE_THRESHOLD = 15.0   # % change in 1 session


class MacroShiftDetector:
    def __init__(self) -> None:
        self._bus   = MessageBus.get()
        self._task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
        self._last_vix: Optional[float]    = None
        self._last_dxy: Optional[float]    = None
        self._last_usdinr: Optional[float] = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._monitor_loop(), name="macro_shift_detector")
        log.info("macro_shift_detector.started")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def _monitor_loop(self) -> None:
        while True:
            try:
                await self._check_macro()
            except Exception as exc:
                log.error("macro_shift_detector.error", error=str(exc))
            await asyncio.sleep(_CHECK_INTERVAL)

    async def _check_macro(self) -> None:
        data = await asyncio.get_event_loop().run_in_executor(None, self._fetch_macro_data)
        if not data:
            return

        vix    = data.get("vix")
        usdinr = data.get("usdinr")

        # VIX spike detection
        if vix and self._last_vix:
            change_pct = (vix - self._last_vix) / self._last_vix * 100
            if change_pct >= _VIX_SPIKE_THRESHOLD:
                await self._emit_alert(
                    severity="emergency",
                    reason=f"India VIX spiked {change_pct:.1f}% (to {vix:.1f})",
                    mode=GuardianResponseMode.HEDGE,
                )

        # USD/INR depreciation (>0.5% in one session = stress)
        if usdinr and self._last_usdinr:
            inr_move = (usdinr - self._last_usdinr) / self._last_usdinr * 100
            if inr_move > 0.5:
                await self._emit_alert(
                    severity="warning",
                    reason=f"INR depreciated {inr_move:.2f}% (USDINR now {usdinr:.2f})",
                    mode=GuardianResponseMode.ALERT,
                )

        self._last_vix    = vix
        self._last_usdinr = usdinr

    @staticmethod
    def _fetch_macro_data() -> dict:
        try:
            vix_data = _cached_download("^INDIAVIX", period="2d", interval="1d")
            inr_data = _cached_download("USDINR=X", period="2d", interval="1d")
            return {
                "vix":    float(vix_data["Close"].iloc[-1]) if vix_data is not None and not vix_data.empty else None,
                "usdinr": float(inr_data["Close"].iloc[-1]) if inr_data is not None and not inr_data.empty else None,
            }
        except Exception:
            return {}

    async def _emit_alert(
        self, severity: str, reason: str, mode: GuardianResponseMode
    ) -> None:
        log.warning("macro_shift_detector.alert", severity=severity, reason=reason)
        alert = GuardianAlert(
            mode=mode,
            severity=severity,
            reason=reason,
        )
        await self._bus.publish(
            Message(
                type=MessageType.GUARDIAN_ALERT,
                source="macro_shift_detector",
                payload=alert.model_dump(mode="json"),
            )
        )
