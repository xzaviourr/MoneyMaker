"""InsiderFlowAgent — Form 4 / BSE bulk deal filings, cluster buying, open-market purchases only."""
from __future__ import annotations

import asyncio
from typing import Optional

import structlog

from ...shared.market_data_cache import get_insider_transactions
from ...shared.schemas import Exchange, SignalDirection, StrategySignal
from .base_strategy import BaseStrategy

log = structlog.get_logger(__name__)


class InsiderFlowAgent(BaseStrategy):
    name = "insider_flow"
    default_expiry_hours = 240  # 10 days

    async def analyse(self, symbol: str, exchange: Exchange) -> Optional[StrategySignal]:
        data = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._fetch_insider(symbol)
        )
        if not data:
            return None

        net_shares  = data.get("net_insider_shares", 0)
        total_value = data.get("total_value_inr", 0)

        if abs(net_shares) == 0:
            return None

        direction  = SignalDirection.LONG if net_shares > 0 else SignalDirection.SHORT
        conviction = min(0.75, 0.4 + min(abs(net_shares) / 1_000_000, 1.0) * 0.35)

        return StrategySignal(
            strategy_name=self.name,
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            conviction=conviction,
            timeframe="1m",  # longer-term
            rationale=f"Insider {'buying' if direction == SignalDirection.LONG else 'selling'}: {net_shares:,} shares",
            supporting_indicators=data,
            expires_at=self._make_expiry(),
        )

    @staticmethod
    def _fetch_insider(symbol: str) -> Optional[dict]:
        try:
            insider = get_insider_transactions(symbol)
            if insider is None or insider.empty or "Transaction" not in insider.columns:
                return None
            # Net open-market purchases (ignore option exercises)
            open_market = insider[insider["Transaction"].str.contains("Purchase|Sale", na=False)]
            net_shares = int(open_market["Shares"].sum()) if not open_market.empty else 0
            return {"net_insider_shares": net_shares, "total_value_inr": 0}
        except Exception:
            return None
