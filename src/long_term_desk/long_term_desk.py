"""
LongTermDesk — top-level orchestrator that wires strategies → SignalAggregator →
Room 1 (Idea) → Room 2 (Capital) → Room 3 (Execution).

One iteration:
  1. Scan universe → ingest signals into aggregator
  2. Pop best idea from queue
  3. Room 1: debate → IdeaVerdict
  4. Room 2: allocate → AllocationPlan
  5. Room 3: execute → ExecutionPlan
  6. Post-trade audit
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional

import structlog
from ..brokers.broker_gateway import BrokerGateway
from ..shared import feature_toggles
from ..shared import market_data_cache
from ..shared.config import toml_cfg
from ..shared.message_bus import MessageBus
from ..shared.schemas import (
    Exchange,
    IdeaQueueItem,
    IdeaVerdict,
    Message,
    MessageType,
    SignalDirection,
    StrategySignal,
)
from .signal_aggregator import SignalAggregator
from .strategies import ALL_STRATEGIES, BaseStrategy
from .room1_idea import (
    OpportunityScout, BullAdvocate, BearAdvocate,
    DevilsAdvocate, SectorSpecialist, MomentumAnalyst, CommitteeChair,
)
from .room2_capital import (
    PortfolioCartographer, LiquidationStrategist,
    OpportunityCostAnalyst, PositionSizer, CostBasisAccountant, AllocationChair,
)
from .room3_execution import (
    RiskGatekeeper, TailRiskSentinel, MarketTimer,
    ExecutionTrader, PostTradeAuditor,
)

log = structlog.get_logger(__name__)


class LongTermDesk:
    """Orchestrates the full long-term deliberation pipeline."""

    def __init__(self) -> None:
        cfg = toml_cfg.get("long_term_desk", {})
        self._universe      = list(cfg.get("universe", []))
        self._scan_interval = int(cfg.get("scan_interval_minutes", 60)) * 60
        self._exchange      = Exchange(cfg.get("exchange", Exchange.NSE.value))

        self._aggregator    = SignalAggregator()
        self._strategies: list[BaseStrategy] = [S() for S in ALL_STRATEGIES]

        # Room 1
        self._scout    = OpportunityScout()
        self._bull     = BullAdvocate()
        self._bear     = BearAdvocate()
        self._devil    = DevilsAdvocate()
        self._sector   = SectorSpecialist()
        self._momentum = MomentumAnalyst()
        self._chair1   = CommitteeChair()

        # Room 2
        self._cartographer   = PortfolioCartographer()
        self._liquidator     = LiquidationStrategist()
        self._opp_cost       = OpportunityCostAnalyst()
        self._sizer          = PositionSizer()
        self._cost_acct      = CostBasisAccountant()
        self._alloc_chair    = AllocationChair()

        # Room 3
        self._risk_gate  = RiskGatekeeper()
        self._tail_risk  = TailRiskSentinel()
        self._timer      = MarketTimer()
        self._trader     = ExecutionTrader()
        self._auditor    = PostTradeAuditor()

        self._bus        = MessageBus.get()
        self._running    = False

        # Without this, a stock that still looks attractive gets treated as a
        # brand new idea every single scan cycle (every 10 min) — no memory
        # that it was just fully debated an hour ago. That's what let
        # COALINDIA get re-debated and re-bought 10 times in one day. This
        # doesn't block a stock forever, just stops it coming back around
        # faster than a real thesis could plausibly change.
        self._last_debated: dict[str, datetime] = {}
        self._debate_cooldown = timedelta(
            hours=int(toml_cfg.get("long_term_desk", {}).get("debate_cooldown_hours", 4))
        )

    async def start(self) -> None:
        self._running = True
        log.info("lt_desk.started", universe_size=len(self._universe))
        asyncio.create_task(self._scan_loop())
        asyncio.create_task(self._deliberation_loop())

    async def stop(self) -> None:
        self._running = False

    # ── Scan loop ─────────────────────────────────────────────────────────

    async def _scan_loop(self) -> None:
        while self._running:
            try:
                if feature_toggles.is_enabled("long_term_desk"):
                    await self._scan_universe()
            except Exception:
                log.exception("lt_desk.scan_loop_error")
            await asyncio.sleep(self._scan_interval)

    async def _scan_universe(self) -> None:
        # The static config.toml universe is the guaranteed baseline — real
        # top-movers are added on top of it each scan, not instead of it, so
        # the candidate pool isn't limited to the same fixed 30 names every
        # single day. Best-effort: never lets a screener failure block the
        # scan of the real universe.
        movers: list[str] = []
        try:
            from .market_movers import fetch_top_movers
            loop = asyncio.get_event_loop()
            movers = await loop.run_in_executor(None, fetch_top_movers)
        except Exception:
            log.exception("lt_desk.movers_fetch_error")

        scan_symbols = list(self._universe) + [m for m in movers if m not in self._universe]
        log.info("lt_desk.scanning", symbols=len(scan_symbols), movers_added=len(movers))
        tasks = []
        for symbol in scan_symbols:
            for strategy in self._strategies:
                tasks.append(self._run_strategy(strategy, symbol))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        signals_ingested = sum(1 for r in results if r is True)
        log.info("lt_desk.scan_complete", signals=signals_ingested)

    async def _run_strategy(self, strategy: BaseStrategy, symbol: str) -> bool:
        try:
            signal = await strategy.analyse(symbol, self._exchange)
            if signal:
                await self._aggregator.ingest(signal)
                return True
        except Exception:
            log.exception("lt_desk.strategy_error",
                          strategy=strategy.name, symbol=symbol)
        return False

    # ── News input ────────────────────────────────────────────────────────
    # Called by NewsWatchdog so a real headline can become a debated idea,
    # the same way a technical/fundamental strategy signal does — news isn't
    # a separate side-channel, it feeds the exact same Room 1 committee.

    async def ingest_news_signal(
        self, symbol: str, direction: SignalDirection,
        conviction: float, rationale: str,
    ) -> None:
        signal = StrategySignal(
            strategy_name="news_sentiment",
            symbol=symbol,
            exchange=self._exchange,
            direction=direction,
            conviction=conviction,
            timeframe="event",
            rationale=rationale,
        )
        await self._aggregator.ingest(signal)
        log.info("lt_desk.news_signal_ingested", symbol=symbol,
                 direction=direction.value, conviction=conviction)

    # ── Deliberation loop ─────────────────────────────────────────────────

    async def _deliberation_loop(self) -> None:
        while self._running:
            if self._aggregator.queue_size() > 0 and feature_toggles.is_enabled("long_term_desk"):
                try:
                    await self._process_next_idea()
                except Exception:
                    log.exception("lt_desk.deliberation_error")
            await asyncio.sleep(30)

    async def _process_next_idea(self) -> None:
        idea = await self._aggregator.pop_idea()
        if not idea:
            return

        last = self._last_debated.get(idea.symbol)
        if last and datetime.utcnow() - last < self._debate_cooldown:
            log.info("lt_desk.idea_on_cooldown", symbol=idea.symbol,
                     next_eligible=(last + self._debate_cooldown).isoformat())
            return

        log.info("lt_desk.processing_idea",
                 symbol=idea.symbol, conviction=idea.conviction_score)
        self._last_debated[idea.symbol] = datetime.utcnow()

        # ── Room 1 ────────────────────────────────────────────────────────
        from ..audit.explainability_ledger import ExplainabilityLedger
        ledger = ExplainabilityLedger.get()

        brief = await self._scout.brief(idea)
        await ledger.record(
            agent_id="room1.opportunity_scout",
            decision=brief.get("recommended_position_type", "swing"),
            reasoning=brief.get("thesis_summary", ""),
            symbol=idea.symbol,
            inputs={"direction": idea.direction.value, "conviction": round(idea.conviction_score, 2)},
            outputs={k: brief.get(k) for k in (
                "thesis_summary", "bull_points", "bear_points",
                "initial_conviction", "recommended_position_type", "data_gaps",
            )},
        )

        bull = await self._bull.argue(idea, brief)
        await ledger.record(
            agent_id="room1.bull_advocate",
            decision=f"+{bull.get('price_target_pct_upside', 0):.1f}%",
            reasoning=bull.get("bull_case", ""),
            symbol=idea.symbol,
            inputs={"thesis": brief.get("thesis_summary", "")[:120]},
            outputs={k: bull.get(k) for k in (
                "price_target_pct_upside", "time_horizon_weeks",
                "key_catalysts", "technical_support", "conviction_score",
            )},
        )

        bear = await self._bear.argue(idea, brief, bull)
        await ledger.record(
            agent_id="room1.bear_advocate",
            decision=f"-{bear.get('max_downside_pct', 0):.1f}%",
            reasoning=bear.get("bear_case", ""),
            symbol=idea.symbol,
            inputs={"bull_target": bull.get("price_target_pct_upside", 0)},
            outputs={k: bear.get(k) for k in (
                "max_downside_pct", "key_risks",
                "invalidation_scenario", "technical_resistance", "conviction_score",
            )},
        )

        devil = await self._devil.stress_test(idea, brief, bull, bear)
        await ledger.record(
            agent_id="room1.devils_advocate",
            decision=devil.get("go_no_go_lean", "conditional"),
            reasoning="; ".join(filter(None, [devil.get("bull_flaw", ""), devil.get("bear_flaw", "")])),
            symbol=idea.symbol,
            inputs={},
            outputs={k: devil.get(k) for k in (
                "hidden_assumptions", "tail_risks", "liquidity_concerns",
                "bull_flaw", "bear_flaw", "stress_test_score", "go_no_go_lean",
            )},
        )

        sector = await self._sector.assess(idea, brief)
        await ledger.record(
            agent_id="room1.sector_specialist",
            decision=sector.get("specialist_verdict", "neutral"),
            reasoning=sector.get("peer_comparison", ""),
            symbol=idea.symbol,
            inputs={"sector": sector.get("sector", "")},
            outputs={k: sector.get(k) for k in (
                "sector", "specialist_verdict", "sector_rotation_signal",
                "sector_conviction_modifier", "peer_comparison",
            )},
        )

        momentum = await self._momentum.assess(idea, brief, bull)
        await ledger.record(
            agent_id="room1.momentum_analyst",
            decision=momentum.get("trend_quality", "neutral"),
            reasoning=momentum.get("chart_pattern", ""),
            symbol=idea.symbol,
            inputs={},
            outputs={k: momentum.get(k) for k in (
                "trend_quality", "momentum_phase", "technical_score",
                "momentum_conviction_modifier", "chart_pattern",
            )},
        )

        verdict: IdeaVerdict = await self._chair1.deliberate(
            idea, brief, bull, bear, devil, sector, momentum
        )

        if not verdict.approved:
            log.info("lt_desk.idea_rejected",
                     symbol=idea.symbol, reason=verdict.reasoning[:80])
            await self._bus.publish(Message(
                type=MessageType.IDEA_REJECTED,
                payload=verdict.model_dump(),
                source="lt_desk.room1",
            ))
            return

        await self._bus.publish(Message(
            type=MessageType.IDEA_APPROVED,
            payload=verdict.model_dump(),
            source="lt_desk.room1",
        ))

        # ── Get current price ─────────────────────────────────────────────
        current_price = await self._get_current_price(idea.symbol)
        if current_price <= 0:
            log.warning("lt_desk.no_price", symbol=idea.symbol)
            await self._record_outcome(idea.symbol, "NOT EXECUTED — no real market price available")
            return

        # ── Room 2 ────────────────────────────────────────────────────────
        cartographer  = await self._cartographer.map(verdict)
        position_size = await self._sizer.compute(verdict, bull, bear, cartographer, current_price)
        capital_needed_pct = (
            float(position_size.get("applied_fraction", 0)) * 100
        )
        liquidation  = await self._liquidator.identify_trims(
            verdict, capital_needed_pct, cartographer
        )
        opp_cost     = await self._opp_cost.assess(verdict, bull, bear)
        cost_basis   = await self._cost_acct.compute(verdict, position_size, current_price)
        alloc_plan   = await self._alloc_chair.finalise(
            verdict, position_size, cost_basis, cartographer, opp_cost, liquidation,
            bull_case=bull, bear_case=bear,
        )

        if alloc_plan is None:
            log.info("lt_desk.allocation_failed", symbol=idea.symbol)
            await self._record_outcome(
                idea.symbol, f"NOT EXECUTED — {self._alloc_chair.last_skip_reason}"
            )
            await self._track_rejection(idea.symbol, current_price, self._alloc_chair.last_skip_reason, "room2")
            return

        # ── Room 3 ────────────────────────────────────────────────────────
        snap = await self._get_lt_pillar_total()
        risk_check  = await self._risk_gate.check(alloc_plan, opp_cost, cartographer, snap)
        tail_check  = await self._tail_risk.check(alloc_plan)
        timing      = await self._timer.advise(alloc_plan)
        exec_plan   = await self._trader.execute(alloc_plan, timing, risk_check, tail_check, rationale=verdict.reasoning)
        await self._auditor.audit(alloc_plan, exec_plan, current_price)

        # Tie the reasoning back to what actually happened — bought or not,
        # how much, at what target/stop — instead of leaving "approved" as
        # the last visible word on a decision that may not have executed.
        # AllocationChair already reserved alloc_plan.allocated_capital in
        # CapitalTracker before Room 3 ran. Room 3 blocking/deferring (risk
        # gate, tail risk, market-closed timing) left that reservation in
        # place forever with no release path — capital silently vanished
        # from the long_term pillar on every rejected idea, even though
        # nothing was ever actually bought. Release whatever wasn't spent.
        from ..supervisor.capital_tracker import CapitalTracker

        filled = [o for o in exec_plan.orders_placed if o.get("average_fill_price")]
        if filled:
            total_qty   = sum(o.get("filled_quantity", 0) for o in filled)
            avg_price   = sum(float(o["average_fill_price"]) * o.get("filled_quantity", 0) for o in filled) / max(total_qty, 1)
            filled_value = avg_price * total_qty
            unused       = alloc_plan.allocated_capital - filled_value
            if unused > 0:
                await CapitalTracker.get().release_lt_desk(idea.symbol, unused)
            target      = current_price * (1 + alloc_plan.target_pct_upside / 100)
            stop        = current_price * (1 - alloc_plan.stop_loss_pct_downside / 100)
            await self._record_outcome(
                idea.symbol,
                f"BOUGHT {total_qty} @ ₹{avg_price:,.2f} — target ₹{target:,.2f}, stop ₹{stop:,.2f}",
            )
        else:
            await CapitalTracker.get().release_lt_desk(idea.symbol, alloc_plan.allocated_capital)
            reason = exec_plan.reason or exec_plan.status
            await self._record_outcome(idea.symbol, f"NOT EXECUTED — {reason}")
            await self._track_rejection(idea.symbol, current_price, reason, "room3")

    async def _record_outcome(self, symbol: str, outcome: str) -> None:
        from ..audit.explainability_ledger import ExplainabilityLedger
        try:
            await ExplainabilityLedger.get().update_outcome(symbol, "room1.committee_chair", outcome)
        except Exception:
            log.exception("lt_desk.record_outcome_failed", symbol=symbol)

    async def _track_rejection(self, symbol: str, price: float, reason: str, room: str) -> None:
        """Starts tracking a rejected idea's real price so we can later tell
        whether the rejection was a good call or missed opportunity — see
        rejected_idea_tracker.py for the daily check that follows up."""
        if price <= 0:
            return
        from ..audit.explainability_ledger import ExplainabilityLedger
        try:
            await ExplainabilityLedger.get().record_rejection(symbol, price, reason, room)
        except Exception:
            log.exception("lt_desk.track_rejection_failed", symbol=symbol)

    async def _get_current_price(self, symbol: str) -> float:
        try:
            broker = BrokerGateway.get()
            if broker.is_connected:
                quote = await broker.get_quote(symbol, self._exchange)
                return float(quote.ltp) if quote else 0.0
        except Exception:
            pass
        # Fallback — same cache every other price lookup uses, never yfinance directly
        try:
            loop = asyncio.get_event_loop()
            df   = await loop.run_in_executor(
                None,
                lambda: market_data_cache.download(f"{symbol}.NS", period="2d", interval="1d")
            )
            if df is not None and not df.empty:
                close = df["Close"]
                if hasattr(close, "shape") and len(getattr(close, "shape", (1,))) > 1:
                    close = close.iloc[:, 0]
                return float(close.iloc[-1])
        except Exception:
            pass
        return 0.0

    async def _get_lt_pillar_total(self) -> float:
        from ..supervisor.capital_tracker import CapitalTracker
        try:
            snap = await CapitalTracker.get().snapshot()
            lt   = snap.pillar_allocations.get("long_term")
            return float(lt.allocated) if lt else 1_000_000.0
        except Exception:
            return 1_000_000.0
