"""
EventPod — News / earnings reaction plays.
Timeframe: Minutes around catalyst.  Compatible regimes: Around catalyst.
Uses STANDARD LLM tier for news sentiment parsing.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

import structlog

from ...llm.llm_gateway import LLMGateway
from ...shared.config import toml_cfg
from ...shared.schemas import (
    MarketRegimeTrend,
    PodConfig,
    PodState,
    Quote,
    SignalDirection,
    SignalStrength,
    TradeSignal,
)
from ..base_pod import BasePod
from ...brokers.broker_gateway import BrokerGateway

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT = """You are an expert event-driven trader specialising in Indian equities.
Analyse the news/event and the current price action to determine if there is a high-conviction
directional trade opportunity. Respond ONLY in JSON:
{
  "direction": "long" | "short" | "neutral",
  "conviction": 0.0-1.0,
  "stop_loss_pct": 1.0-3.0,
  "rationale": "brief explanation",
  "hold_minutes": 5-120
}
"""


class EventPod(BasePod):
    """
    Signal logic:
    - Receives event triggers from NewsWatchdog via message bus
    - Uses LLM to assess event impact and direction
    - Places quick directional trade if conviction > threshold
    """

    def __init__(self, gateway: BrokerGateway) -> None:
        config = PodConfig(
            pod_id="event_pod",
            pod_name="EventPod",
            strategy="news_reaction",
            timeframe="1m",
            compatible_regimes=list(MarketRegimeTrend),
            state=PodState.SANDBOX,
            max_position_size_pct=10.0,
            stop_loss_pct=2.0,
            max_open_positions=2,
        )
        super().__init__(config, gateway)
        self._pending_events: list[dict] = []
        self._min_conviction = 0.7
        self._watched: set[str] = set()

    def watchlist(self) -> list[tuple[str, str]]:
        # Needs a live quote for a symbol before it can react to a triggered
        # event on it — watch the full universe, not just a handful, so news
        # on any of these companies can actually result in a trade.
        universe = toml_cfg.get("long_term_desk", {}).get("universe", [])
        self._watched = set(universe)
        return [(s, "NSE") for s in universe]

    async def trigger_event(self, symbol: str, exchange: str, news: str) -> None:
        """Called externally by NewsWatchdog when a market-moving event is detected.
        If it's a stock we weren't already watching (e.g. a small-cap that just
        made news), start streaming its quote too, instead of dropping the event."""
        if symbol not in self._watched:
            self._watched.add(symbol)
            self._gateway.add_symbols(self._on_quote, [(symbol, exchange)])

        self._pending_events.append({
            "symbol": symbol,
            "exchange": exchange,
            "news": news,
            "timestamp": datetime.utcnow(),
        })

    async def generate_signal(self, quote: Quote) -> Optional[TradeSignal]:
        # Process any pending events for this symbol
        relevant = [
            e for e in self._pending_events
            if e["symbol"] == quote.symbol
            and (datetime.utcnow() - e["timestamp"]).seconds < 600
        ]
        if not relevant:
            return None

        event = relevant[-1]
        self._pending_events.remove(event)

        try:
            llm = LLMGateway.get()
            result = await llm.complete_json(
                agent_id="pod.strategy_agent",
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=(
                    f"Symbol: {quote.symbol}\n"
                    f"Current Price: {quote.ltp}\n"
                    f"Event/News: {event['news']}\n"
                    f"Volume: {quote.volume}"
                ),
            )
        except Exception as exc:
            log.error("event_pod.llm_error", error=str(exc))
            return None

        direction_str = result.get("direction", "neutral")
        conviction    = float(result.get("conviction", 0.0))

        if direction_str == "neutral" or conviction < self._min_conviction:
            return None

        direction  = SignalDirection.LONG if direction_str == "long" else SignalDirection.SHORT
        sl_pct     = float(result.get("stop_loss_pct", 2.0)) / 100
        hold_min   = int(result.get("hold_minutes", 30))
        sl_mult    = Decimal(str(1 - sl_pct)) if direction == SignalDirection.LONG else Decimal(str(1 + sl_pct))

        return TradeSignal(
            symbol=quote.symbol,
            exchange=quote.exchange,
            direction=direction,
            strength=SignalStrength.STRONG if conviction > 0.8 else SignalStrength.MODERATE,
            strategy=self.config.strategy,
            conviction=conviction,
            entry_price=quote.ltp,
            stop_loss=quote.ltp * sl_mult,
            timeframe=self.config.timeframe,
            expires_at=datetime.utcnow() + timedelta(minutes=hold_min),
            rationale=result.get("rationale", "Event-driven signal"),
        )
