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

import logging.handlers
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
from src.portfolio import PortfolioManager
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
from src.pods.momentum_pod import make_momentum_pod
from src.pods.breakout_pod import make_breakout_pod
from src.pods.mean_reversion_pod import make_mean_reversion_pod
from src.pods.event_pod import make_event_pod

# ── Logging ────────────────────────────────────────────────────────────────────

_SECRET_KEYS = frozenset({
    "azure_openai_api_key", "api_key", "secret_key", "api_secret_key",
    "password", "totp_secret", "password_key", "encryption_key",
    "client_secret", "token", "authorization",
})


def _scrub_secrets(logger, method, event_dict):  # type: ignore[no-untyped-def]
    for key in list(event_dict.keys()):
        if any(s in key.lower() for s in ("key", "secret", "password", "token", "totp")):
            event_dict[key] = "***"
    return event_dict


structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _scrub_secrets,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)
import os as _os
_LOG_DIR = _os.path.join("data", "logs")
_os.makedirs(_LOG_DIR, exist_ok=True)
_file_handler = logging.handlers.RotatingFileHandler(
    _os.path.join(_LOG_DIR, "moneymaker.log"),
    maxBytes=10 * 1024 * 1024,  # 10 MB per file
    backupCount=5,
    encoding="utf-8",
)
logging.basicConfig(
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout), _file_handler],
)

# Silence noisy third-party loggers — keep yfinance at WARNING so fetch
# failures actually appear in the terminal instead of disappearing silently.
logging.getLogger("yfinance").setLevel(logging.WARNING)
logging.getLogger("peewee").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.getLogger("azure.identity").setLevel(logging.CRITICAL)   # Key Vault auth noise
logging.getLogger("azure.core").setLevel(logging.CRITICAL)

log = structlog.get_logger("main")


class FeedbackSystem:
    """Container to pass feedback references to API routes."""
    def __init__(self, analyzer, calibration, review_agent):
        self.analyzer     = analyzer
        self.calibration  = calibration
        self.review_agent = review_agent


def _init_ledger(demo: bool) -> None:
    from src.audit.explainability_ledger import ExplainabilityLedger
    ExplainabilityLedger.init(mode="demo" if demo else "paper")


async def _verify_azure(deployment: str, endpoint: str) -> None:
    """Crash loudly if Azure deployment is unreachable — never silently fall back."""
    import httpx
    from src.shared.config import settings as _s
    url = f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/chat/completions?api-version=2024-12-01-preview"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url,
                headers={"api-key": _s.azure_openai_api_key},
                json={"messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
            )
        if r.status_code == 404:
            print("\n" + "="*60)
            print("STARTUP FAILED — Azure deployment not found.")
            print(f"  Deployment tried : {deployment}")
            print(f"  Endpoint         : {endpoint}")
            print("  Fix: open portal.azure.com → your AI Foundry resource")
            print("       → Deployments tab → copy the exact name → update")
            print("       config.toml [llm.deployments] → restart.")
            print("="*60 + "\n")
            sys.exit(1)
        if r.status_code == 401:
            print("\n" + "="*60)
            print("STARTUP FAILED — Azure API key rejected (401).")
            print("  Check AZURE_OPENAI_API_KEY in your .env file.")
            print("="*60 + "\n")
            sys.exit(1)
    except Exception as exc:
        print(f"\nSTARTUP FAILED — Could not reach Azure: {exc}\n")
        sys.exit(1)
    log.info("llm.azure_verified", deployment=deployment)


def _init_llm(demo: bool) -> LLMGateway:
    if demo:
        from src.llm.mock_provider import MockLLMProvider
        log.info("llm.using_mock_provider", reason="demo_mode")
        return LLMGateway.init(MockLLMProvider())

    from src.shared.config import settings as _settings
    if not _settings.azure_openai_api_key:
        print("\n" + "="*60)
        print("STARTUP FAILED — AZURE_OPENAI_API_KEY not set in .env.")
        print("  Use --demo to run without Azure credentials.")
        print("="*60 + "\n")
        sys.exit(1)

    log.info("llm.using_azure_openai")
    return LLMGateway.init()


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

    pods = [
        make_momentum_pod(broker),
        make_breakout_pod(broker),
        make_mean_reversion_pod(broker),
        make_event_pod(broker),
    ]

    capital = CapitalTracker.get()
    # per_pod_budget in config.toml is a cap, not a fixed draw — it was
    # previously allocated as-is regardless of the intraday pillar's real
    # size, which only ever worked by coincidence at the default ₹10L
    # capital (₹4L intraday / 4 pods = exactly ₹1L each). Any other total
    # capital (e.g. a smaller second portfolio) either overdraws the pillar
    # and crashes on the last pod, or leaves it under-used. Split the real
    # pillar evenly across pods instead, capped at the configured ceiling.
    configured_cap = Decimal(str(toml_cfg.get("pods", {}).get("per_pod_budget", 100_000)))
    intraday_total = await capital.available_in_pillar("intraday")
    fair_share     = (intraday_total / len(pods)) if pods else Decimal("0")
    per_pod_budget = min(configured_cap, fair_share)

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


def _check_yahoo_finance() -> None:
    """Quick startup check — fetches one price to confirm Yahoo Finance is reachable.
    Prints a visible warning (not an error) so the system still starts but you
    immediately know if live data will be missing."""
    from src.shared.market_data_cache import get_quote
    print("Checking Yahoo Finance connectivity...", flush=True)
    price = get_quote("RELIANCE", "NSE")
    if price:
        print(f"  Yahoo Finance OK — RELIANCE.NS = ₹{price:.2f}", flush=True)
    else:
        print("\n" + "=" * 60, flush=True)
        print("WARNING — Yahoo Finance returned no price for RELIANCE.NS.", flush=True)
        print("  Dashboard will show stale data until a price can be fetched.", flush=True)
        print("  Possible causes:", flush=True)
        print("    1. Market is closed (data still works, previous close used)", flush=True)
        print("    2. yfinance needs upgrading: pip install --upgrade yfinance", flush=True)
        print("    3. Yahoo Finance is rate-limiting this IP (auto-retry in 1h)", flush=True)
        print("  System will start anyway. Watch the Logs page for yfinance errors.", flush=True)
        print("=" * 60 + "\n", flush=True)


async def _boot(paper: bool = False, api_only: bool = False, demo: bool = False) -> None:
    log.info("moneymaker.boot_start", paper=paper, api_only=api_only, demo=demo)
    _check_yahoo_finance()

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

    # 3. Ledger mode — tag all decisions with demo/paper before anything writes
    _init_ledger(demo=demo)

    # 4. LLM Gateway — verify Azure is reachable before booting (paper mode only)
    if not demo:
        from src.llm.azure_openai.deployment_map import get_deployment
        from src.shared.schemas import LLMTier
        from src.shared.config import settings as _s
        await _verify_azure(get_deployment(LLMTier.FAST), _s.azure_openai_endpoint)
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

    # 10. Portfolio Manager (tracks fills, evaluates news, manages exits)
    portfolio_manager = PortfolioManager.get()
    await portfolio_manager.start()

    # 11. Firm CIO
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
    try:
        await server.serve()
    finally:
        log.info("moneymaker.shutting_down")
        try:
            await MessageBus.get().stop()
        except Exception:
            pass
        try:
            await BrokerGateway.get().disconnect()
        except Exception:
            pass
        log.info("moneymaker.shutdown_complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MoneyMaker Trading System")
    parser.add_argument("--paper",    action="store_true", help="Use paper broker (real LLM)")
    parser.add_argument("--demo",     action="store_true", help="Demo mode: mock LLM + paper broker, no credentials needed")
    parser.add_argument("--api-only", action="store_true", help="Start API only, no trading")
    args = parser.parse_args()

    asyncio.run(main(paper=args.paper, api_only=args.api_only, demo=args.demo))
