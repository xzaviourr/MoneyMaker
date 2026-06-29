"""
MoneyMaker — System entry point.

Start order:
  1. Config + logging
  2. MessageBus
  3. Capital Tracker (initialise pillars)
  4. LLM Gateway (mock in demo mode, Azure in production)
  5. Broker Gateway (paper in demo/paper mode, real in production)
  6. Data foundation (DataSentinel, RegimeClassifier)
  7. Circuit Breaker
  8. Pod Supervisor + Pods
  9. Long-Term Desk
  10. Portfolio Guardian
  11. Firm CIO
  12. Feedback System
  13. FastAPI (Mission Control)

Usage:
  python main.py                    # full system (requires Azure + 5Paisa creds)
  python main.py --paper            # real LLM + paper broker
  python main.py --demo             # mock LLM + paper broker (no credentials needed)
  python main.py --api-only         # API + UI without trading
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from typing import Optional

# The project path contains non-ASCII characters (OneDrive\ドキュメント\...), and
# Windows' console defaults to the legacy cp1252 codepage. Any exception whose
# traceback includes that path — which is every traceback, since it's always
# somewhere in the call stack — crashes logging.StreamHandler.emit() with a
# UnicodeEncodeError instead of just printing the error, silently killing the
# whole process. Force UTF-8 with a safe fallback so a log line never takes
# the server down with it.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="backslashreplace")

import structlog
import uvicorn

from src.shared.config import settings, toml_cfg
from src.shared.message_bus import MessageBus
from src.supervisor.capital_tracker import CapitalTracker
from src.foundation.regime_classifier import RegimeClassifier
from src.foundation.data_sentinel import DataSentinel
from src.brokers.broker_gateway import BrokerGateway
from src.llm.llm_gateway import LLMGateway
from src.supervisor.circuit_breaker import CircuitBreaker
from src.supervisor.pod_supervisor import PodSupervisor
from src.supervisor.firm_cio import FirmCIO
from src.long_term_desk import LongTermDesk
from src.guardian.portfolio_guardian import PortfolioGuardian
from src.feedback import (
    TradeAttributionEngine,
    StrategyPerformanceAnalyzer,
    ParameterOptimizer,
    AgentCalibrationEngine,
    VoteWeightUpdater,
    OutcomeAttributionTimer,
    RegimeAdjustedScorer,
    SystemReviewAgent,
)
from src.api.main import app
from src.api.routes.pods import set_pod_supervisor
from src.api.routes.feedback import set_feedback_system
from src.api.routes.system import set_system_refs

# ── Logging ────────────────────────────────────────────────────────────────────

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

# Silence noisy third-party loggers
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("peewee").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.CRITICAL)

log = structlog.get_logger("main")


class FeedbackSystem:
    """Container to pass feedback references to API routes."""
    def __init__(self, analyzer, calibration, review_agent):
        self.analyzer     = analyzer
        self.calibration  = calibration
        self.review_agent = review_agent


def _init_llm(demo: bool) -> LLMGateway:
    """Initialise LLM gateway. Falls back to mock if no Azure credentials."""
    if demo:
        from src.llm.mock_provider import MockLLMProvider
        log.info("llm.using_mock_provider", reason="demo_mode")
        return LLMGateway.init(MockLLMProvider())

    from src.shared.config import settings as _settings
    azure_key = _settings.azure_openai_api_key
    if not azure_key:
        from src.llm.mock_provider import MockLLMProvider
        log.warning(
            "llm.no_azure_key_found",
            msg="AZURE_OPENAI_API_KEY not set — falling back to MockLLMProvider. "
                "Set the env var and restart for real LLM analysis.",
        )
        return LLMGateway.init(MockLLMProvider())

    log.info("llm.using_azure_openai")
    return LLMGateway.init()  # uses AzureOpenAIProvider()


async def _watch_yahoo_fetches(bus: MessageBus) -> None:
    """market_data_cache is sync and runs on worker threads as well as
    inline in async code, so it can't safely call the async bus itself.
    This polls its plain-variable handoff and republishes as a real bus
    event — the only way the Yahoo Finance node can show up as 'live'
    on the Flow page, the same way news/trades/decisions already do."""
    from src.shared.market_data_cache import get_last_fetch
    from src.shared.schemas import Message, MessageType
    last_seen: float | None = None
    while True:
        fetch = get_last_fetch()
        if fetch and fetch["ts"] != last_seen:
            last_seen = fetch["ts"]
            await bus.publish(Message(
                type=MessageType.DATA_FETCHED, source="yahoo_finance",
                payload={"detail": fetch["detail"]},
            ))
        await asyncio.sleep(2.0)


async def _register_pods(pod_supervisor: PodSupervisor, broker: BrokerGateway) -> list:
    """Create and register all intraday pods, each with a real capital_budget
    drawn from the 'intraday' pillar — without this, every pod's position
    sizing computes to 0 and every signal silently gets skipped, no matter
    what the strategy or regime says."""
    from decimal import Decimal
    from src.pods.momentum_pod import make_momentum_pod       # noqa: PLC0415
    from src.pods.breakout_pod import make_breakout_pod       # noqa: PLC0415
    from src.pods.mean_reversion_pod import make_mean_reversion_pod  # noqa: PLC0415
    from src.pods.event_pod import make_event_pod             # noqa: PLC0415

    pods = [
        make_momentum_pod(broker),
        make_breakout_pod(broker),
        make_mean_reversion_pod(broker),
        make_event_pod(broker),
    ]

    capital = CapitalTracker.get()
    per_pod_budget = Decimal("100000")  # 4 pods x ₹1,00,000 = the full ₹4,00,000 intraday pillar

    classifier = RegimeClassifier.get()
    for pod in pods:
        await capital.allocate_to_pod("intraday", pod.pod_id, per_pod_budget)
        pod.config.capital_budget = per_pod_budget
        await pod.start(regime_classifier=classifier)
        await pod_supervisor.register_pod(pod)
        log.info("pod.registered", pod_id=pod.pod_id, strategy=pod.config.strategy,
                 capital_budget=str(per_pod_budget))

    log.info("pods.all_registered", count=len(pods))
    return pods


async def _boot(paper: bool = False, api_only: bool = False, demo: bool = False) -> None:
    log.info("moneymaker.boot_start", paper=paper, api_only=api_only, demo=demo)

    # 1. MessageBus + FlowTracker (records every message for the graph dashboard)
    bus = MessageBus.get()
    from src.shared.flow_tracker import FlowTracker
    flow_tracker = FlowTracker.get()
    bus.subscribe_all(flow_tracker.handle)
    await bus.start()  # starts the dispatch loop — without this, publish() just queues
                        # messages forever and NO subscriber ever receives anything
    asyncio.create_task(_watch_yahoo_fetches(bus))

    # 2. Capital
    tracker = CapitalTracker.get()
    await tracker.initialise()

    # 3. LLM Gateway — must come before anything that calls LLMGateway.get()
    llm = _init_llm(demo=demo)
    log.info("llm.gateway_ready", provider=llm._provider.name if hasattr(llm._provider, "name") else type(llm._provider).__name__)

    # 4. Broker
    use_paper = paper or demo  # demo always uses paper broker
    broker = BrokerGateway.from_config(paper=use_paper)
    await broker.connect()
    log.info("broker.connected", paper=use_paper)

    if api_only:
        log.info("api_only_mode")
        return

    # 5. Data foundation
    sentinel = DataSentinel()
    await sentinel.start()

    # Use get() so the singleton is set before pods call RegimeClassifier.get()
    classifier = RegimeClassifier.get()
    await classifier.start()

    # 6. Circuit Breaker
    breaker = CircuitBreaker.get()
    await breaker.start()

    # 7. Pod Supervisor + Pods
    pod_supervisor = PodSupervisor()
    pods = await _register_pods(pod_supervisor, broker)
    event_pod = next(p for p in pods if p.pod_id == "event_pod")
    await pod_supervisor.start()
    set_pod_supervisor(pod_supervisor)

    # 8. Long-Term Desk
    lt_desk = LongTermDesk()
    await lt_desk.start()
    set_system_refs(pod_supervisor=pod_supervisor, llm_gateway=llm, broker_gateway=broker, lt_desk=lt_desk)

    # 9. Portfolio Guardian
    guardian = PortfolioGuardian(gateway=broker)
    guardian.set_event_pod(event_pod)      # News Watchdog feeds every headline to EventPod
    guardian.set_long_term_desk(lt_desk)   # ...and into Room 1's debate, not just fast trades
    await guardian.start()
    set_system_refs(pod_supervisor=pod_supervisor, llm_gateway=llm, broker_gateway=broker, lt_desk=lt_desk, guardian=guardian)

    # 10. Firm CIO
    cio = FirmCIO()
    await cio.start()

    # 11. Feedback System
    attribution_engine = TradeAttributionEngine()
    await attribution_engine.start()

    analyzer = StrategyPerformanceAnalyzer()
    await analyzer.start()

    calibration = AgentCalibrationEngine()
    await calibration.start()

    weight_updater = VoteWeightUpdater(calibration)
    await weight_updater.start()

    attr_timer = OutcomeAttributionTimer(attribution_engine)
    await attr_timer.start()

    scorer    = RegimeAdjustedScorer(analyzer)
    optimizer = ParameterOptimizer(analyzer)

    review_agent = SystemReviewAgent(analyzer, calibration, scorer)
    await review_agent.start()

    fb_system = FeedbackSystem(analyzer, calibration, review_agent)
    set_feedback_system(fb_system)

    log.info("moneymaker.all_systems_online")


async def main(paper: bool = False, api_only: bool = False, demo: bool = False) -> None:
    await _boot(paper=paper, api_only=api_only, demo=demo)

    # API server (non-blocking)
    api_cfg = toml_cfg.get("api", {})
    host    = api_cfg.get("host", "0.0.0.0")
    port    = int(api_cfg.get("port", 8000))

    config  = uvicorn.Config(app, host=host, port=port, log_level="info")
    server  = uvicorn.Server(config)

    def _shutdown(sig, frame):
        log.info("moneymaker.shutdown_signal", sig=sig)
        server.should_exit = True

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info("api.starting", host=host, port=port)
    await server.serve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MoneyMaker Trading System")
    parser.add_argument("--paper",    action="store_true", help="Use paper broker (real LLM)")
    parser.add_argument("--demo",     action="store_true", help="Demo mode: mock LLM + paper broker, no credentials needed")
    parser.add_argument("--api-only", action="store_true", help="Start API only, no trading")
    args = parser.parse_args()

    asyncio.run(main(paper=args.paper, api_only=args.api_only, demo=args.demo))
