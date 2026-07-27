"""
/system/graph — full data-lineage graph for the React Flow dashboard.

Every node exposes:
  inputs  — what data arrived, from which node, and when
  state   — what this node is currently computing / holding
  outputs — what it last sent out, to which node, and when
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from ...shared import feature_toggles

router = APIRouter()

_pod_supervisor: Any = None
_llm_gateway:    Any = None
_broker_gateway: Any = None
_lt_desk:        Any = None
_guardian:       Any = None
_start_time: float = time.time()


@router.get("/toggles")
async def get_toggles() -> dict:
    return feature_toggles.get_all()


@router.post("/toggles/{name}")
async def set_toggle(name: str, body: dict) -> dict:
    try:
        feature_toggles.set_enabled(name, bool(body.get("enabled", True)))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return feature_toggles.get_all()


def set_system_refs(pod_supervisor=None, llm_gateway=None, broker_gateway=None, lt_desk=None, guardian=None) -> None:
    global _pod_supervisor, _llm_gateway, _broker_gateway, _lt_desk, _guardian
    _pod_supervisor  = pod_supervisor
    _llm_gateway     = llm_gateway
    _broker_gateway  = broker_gateway
    _lt_desk         = lt_desk
    _guardian        = guardian


def get_guardian() -> Any:
    return _guardian


def get_lt_desk() -> Any:
    return _lt_desk


@router.get("/queue")
async def get_idea_queue() -> dict:
    """What's currently lined up waiting for a Room 1 debate (Long-Term Desk),
    plus each intraday pod's current status — built for manually watching how
    much is queued/being worked through, not just the end results."""
    lt_queue: list[dict] = []
    if _lt_desk is not None:
        try:
            items = _lt_desk._aggregator.peek_queue()
            lt_queue = [
                {
                    "symbol":                item.symbol,
                    "direction":             item.direction.value,
                    "conviction_score":      round(item.conviction_score, 3),
                    "supporting_strategies": item.supporting_strategies,
                    "contradicting_strategies": item.contradicting_strategies,
                    "queued_at":             item.created_at.isoformat(),
                    "expires_at":            item.expires_at.isoformat() if item.expires_at else None,
                }
                for item in items
            ]
        except Exception:
            lt_queue = []

    intraday_pods: list[dict] = []
    if _pod_supervisor is not None:
        try:
            for pod in _pod_supervisor.pods.values():
                m = pod.get_metrics()
                intraday_pods.append({
                    "pod_id":       pod.config.pod_id,
                    "name":         pod.config.pod_name,
                    "state":        pod.config.state.value,
                    "watchlist":    [sym for sym, _exch in pod.watchlist()],
                    "open_positions": len(pod._positions),
                    "trades_today": m.total_trades,
                    "last_updated": m.updated_at.isoformat(),
                })
        except Exception:
            intraday_pods = []

    return {
        "long_term_queue_size": len(lt_queue),
        "long_term_queue":      lt_queue,
        "intraday_pods":        intraday_pods,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _age(ts: float | None) -> str:
    if ts is None:
        return "never"
    s = int(time.time() - ts)
    if s < 5:   return "just now"
    if s < 60:  return f"{s}s ago"
    return f"{s // 60}m ago"

def _inp(from_node: str, label: str, value: str, age: str) -> dict:
    return {"from_node": from_node, "label": label, "value": value, "age": age}

def _out(to_node: str, label: str, value: str, age: str) -> dict:
    return {"to_node": to_node, "label": label, "value": value, "age": age}

def _state(title: str, lines: list[str]) -> dict:
    return {"title": title, "lines": lines}


# ── Main endpoint ─────────────────────────────────────────────────────────────

@router.get("/graph")
async def get_system_graph() -> dict:
    from ...foundation.regime_classifier import RegimeClassifier
    from ...supervisor.capital_tracker import CapitalTracker
    from ...shared.market_data_cache import cache_stats, is_blocked
    from ...shared.flow_tracker import FlowTracker

    ft    = FlowTracker.get()
    now   = datetime.utcnow().isoformat()
    uptime = int(time.time() - _start_time)

    # ── Database (SQLite — market_data.db) ──────────────────────────────────
    from ...shared.service_log import get_logs as _get_db_logs, get_stats as _get_db_stats
    cs_db    = cache_stats()
    log_stats = _get_db_stats()
    db_logs  = _get_db_logs(service="database", limit=1)
    db_age   = _age(db_logs[0]["ts"]) if db_logs else "never"
    db_last  = db_logs[0]["message"] if db_logs else "no activity yet"
    by_service_lines = [
        f"  {svc}: {count}" for svc, count in sorted(log_stats.get("by_service", {}).items())
    ]

    database_enabled = feature_toggles.is_enabled("database")
    database_node = _node(
        id="database", label="Database" if database_enabled else "Database (PAUSED)", ntype="sink",
        status="warn" if (log_stats.get("errors", 0) > 0 or not database_enabled) else "ok",
        inputs=[
            _inp("yahoo_finance", "Quote/OHLCV writes", "cached rows", db_age),
            _inp("five_paisa",    "Order/session writes", "if active broker", db_age),
            _inp("news",          "Headline cache writes", "30min TTL", db_age),
        ],
        state=_state(
            "SQLite — market_data.db",
            [
                f"File:            {cs_db.get('db_path', 'market_data.db')}",
                f"File size:       {cs_db.get('size_mb', 0)} MB",
                f"market_cache:    {cs_db.get('valid_entries', 0)} valid / {cs_db.get('total_entries', 0)} total rows",
                f"service_logs:    {log_stats.get('total_log_rows', 0)} total rows",
                "Logged calls by service:",
                *by_service_lines,
                f"Errors logged:   {log_stats.get('errors', 0)}",
                f"Warnings logged: {log_stats.get('warnings', 0)}",
                f"Last write:      {db_last}",
                f"Last write age:  {db_age}",
            ]
        ),
        outputs=[
            _out("data_sentinel", "Cached OHLCV/quotes", "served on cache hit", db_age),
        ],
    )

    # ── News (Yahoo Finance + Indian financial RSS — all free, no API key) ───
    from ...feeds.rss_news import list_sources as _list_rss_sources
    news_logs = _get_db_logs(service="news", limit=5)
    news_fetch_logs = [l for l in news_logs if l["message"].startswith("Fetched")]
    news_age  = _age(news_logs[0]["ts"]) if news_logs else "never"
    rss_sources = _list_rss_sources()
    recent_headline_lines = [
        f"  {_age(l['ts'])}: {l['message'].split(' — ', 1)[-1] if ' — ' in l['message'] else l['message']}"
        for l in news_fetch_logs[:3]
    ] or ["  (no fetches yet)"]

    news_enabled = feature_toggles.is_enabled("news")
    news_node = _node(
        id="news", label="News" if news_enabled else "News (PAUSED)", ntype="source",
        status="ok" if news_enabled else "warn",
        inputs=[
            _inp("market", "Watched symbols", "held positions + default watchlist", "continuous"),
        ],
        state=_state(
            "Yahoo Finance + Indian financial RSS" if news_enabled else "PAUSED — manually disabled",
            [
                *([] if news_enabled else ["Toggled off from the Flow page — not fetching."]),
                f"Sources: yfinance .news + {', '.join(rss_sources)}",
                f"Per-symbol watchlist: {len(_guardian._news_watchdog._watched_symbols) if _guardian else 0} stocks"
                " (grows dynamically as RSS/Reddit spot new ones, starts at the 30-stock LT Desk universe)",
                "Recently fetched:",
                *recent_headline_lines,
            ]
        ),
        outputs=[
            _out("portfolio_guardian", "GuardianAlert", "if WARNING/EMERGENCY severity", news_age),
        ],
    )

    # ── Reddit (sentiment feed — shared app, rate-limited deliberately) ─────
    from ...feeds.reddit_feed import rate_limit_status as _reddit_rate_status, list_subreddits as _list_subreddits
    reddit_logs = _get_db_logs(service="reddit", limit=5)
    reddit_fetch_logs = [l for l in reddit_logs if l["message"].startswith("Fetched")]
    reddit_age  = _age(reddit_logs[0]["ts"]) if reddit_logs else "never"
    reddit_rate = _reddit_rate_status()
    reddit_recent_lines = [
        f"  {_age(l['ts'])}: {l['message'].split(' — ', 1)[-1] if ' — ' in l['message'] else l['message']}"
        for l in reddit_fetch_logs[:3]
    ] or ["  (no fetches yet)"]
    reddit_enabled = feature_toggles.is_enabled("reddit")
    reddit_status = "ok" if (reddit_rate.get("connected") and reddit_enabled) else "warn"

    reddit_node = _node(
        id="reddit", label="Reddit" if reddit_enabled else "Reddit (PAUSED)", ntype="source",
        status=reddit_status,
        inputs=[
            _inp("market", "Watched subreddits", ", ".join(_list_subreddits()), "continuous"),
        ],
        state=_state(
            "Read-only, self-throttled — shared app" if reddit_enabled else "PAUSED — manually disabled",
            [
                *([] if reddit_enabled else ["Toggled off from the Flow page — not fetching."]),
                f"Connected:        {'yes' if reddit_rate.get('connected') else 'no — REDDIT_CLIENT_ID/SECRET missing'}",
                f"Read-only mode:   {reddit_rate.get('read_only')}",
                f"Min gap/request:  {reddit_rate.get('min_seconds_between_requests')}s",
                f"Subreddits:       {reddit_rate.get('subreddit_count')} ({', '.join(_list_subreddits())})",
                "Poll cycle:       every 5 min → ~0.6 requests/min average, never bursty",
                "Recently fetched:",
                *reddit_recent_lines,
            ]
        ),
        outputs=[
            _out("portfolio_guardian", "GuardianAlert", "if WARNING/EMERGENCY severity", reddit_age),
        ],
    )

    # ── News Extractor ───────────────────────────────────────────────────────
    # Two feeds: gist_feed is the primary one — every single stock-specific
    # headline from any one of {Yahoo Finance per-symbol news, RSS, Reddit}
    # gets its own buy/avoid/watch gist, no waiting for another source to agree.
    # synthesis_feed is a bonus, rarer highlight for when 2+ sources happen to
    # cover the same stock within 20 minutes — both informational only for now,
    # not wired into trading yet.
    extractor_enabled = feature_toggles.is_enabled("news_extractor")
    gist_feed = _guardian._news_watchdog.get_gist_feed() if _guardian else []
    synthesis_feed = _guardian._news_watchdog.get_synthesis_feed() if _guardian else []
    extractor_age = _age(datetime.fromisoformat(gist_feed[0]["ts"]).replace(tzinfo=timezone.utc).timestamp()) if gist_feed else "no headlines processed yet"

    news_extractor_node = _node(
        id="news_extractor", label="News Extractor" if extractor_enabled else "News Extractor (PAUSED)",
        ntype="processor",
        status="ok" if extractor_enabled else "warn",
        inputs=[
            _inp("yahoo_finance", "Per-symbol news",  "yfinance .news headlines", extractor_age),
            _inp("news",          "RSS headlines",    "general market news",      extractor_age),
            _inp("reddit",        "Retail posts",     "subreddit sentiment",      extractor_age),
        ],
        state=_state(
            f"{len(gist_feed)} gists, {len(synthesis_feed)} cross-source matches" if extractor_enabled else "PAUSED — manually disabled",
            [
                *([] if extractor_enabled else ["Toggled off from the Flow page — not processing headlines."]),
                "Every stock-specific headline gets its own buy/avoid/watch gist.",
                "Cross-source matches are a bonus when 2+ sources agree within 20 min.",
                f"Last headline: {extractor_age}",
            ]
        ),
        outputs=[
            _out("lt_desk_room1", "Gist + consolidated reasoning", "context only — not wired into trades yet", extractor_age),
        ],
    )
    news_extractor_node["gist_feed"] = gist_feed
    news_extractor_node["synthesis_feed"] = synthesis_feed

    # ── Yahoo Finance ──────────────────────────────────────────────────────
    cs      = cache_stats()
    blocked = is_blocked()
    yf_enabled = feature_toggles.is_enabled("yahoo_finance")
    yf_status = "warn" if (blocked or not yf_enabled) else "ok"
    yf_logs  = _get_db_logs(service="yahoo_finance", limit=1)
    yf_age   = _age(yf_logs[0]["ts"]) if yf_logs else "never"
    yf_last  = yf_logs[0]["message"] if yf_logs else "no fetches yet"

    yf_node = _node(
        id="yahoo_finance", label="Yahoo Finance" if yf_enabled else "Yahoo Finance (PAUSED)", ntype="source",
        status=yf_status,
        inputs=[
            _inp("market", "NSE market schedule", "Mon–Fri 09:15–15:30 IST", "live"),
        ],
        state=_state(
            "SQLite cache" if (not blocked and yf_enabled) else ("BLOCKED — rate limited" if blocked else "PAUSED — manually disabled"),
            [
                *([] if yf_enabled else ["Toggled off from the Flow page — quotes/OHLCV/info calls return nothing new;",
                                          "existing positions just freeze at their last known price."]),
                f"Valid entries: {cs.get('valid_entries', 0)}",
                f"Total stored: {cs.get('total_entries', 0)}",
                f"DB: {cs.get('db_path', 'market_data.db')}",
                "Blocked: YES (1hr backoff)" if blocked else "Backoff: none",
                f"Last fetch:     {yf_last}",
                f"Last fetch age: {yf_age}",
            ]
        ),
        outputs=[
            _out("data_sentinel", "NIFTY50 OHLCV (60d)", "daily bars", yf_age),
            _out("data_sentinel", "INDIAVIX (5d)",        "daily bars", yf_age),
        ],
    )

    # ── Data Sentinel ──────────────────────────────────────────────────────
    sentinel_ev = ft.last_from("data_sentinel")
    data_sentinel_enabled = feature_toggles.is_enabled("data_sentinel")
    sentinel_node = _node(
        id="data_sentinel", label="Data Sentinel" if data_sentinel_enabled else "Data Sentinel (PAUSED)", ntype="processor",
        status="ok" if data_sentinel_enabled else "warn",
        inputs=[
            _inp("yahoo_finance", "Raw OHLCV",   "price + volume bars", yf_age),
            _inp("yahoo_finance", "Quote ticks", "LTP per symbol",       yf_age),
        ],
        state=_state(
            "Validating market data",
            [
                "Checks: price range, volume > 0, no NaN",
                "Publishes QUOTE_UPDATE to bus",
                f"Last bus event: {sentinel_ev['type'] if sentinel_ev else 'none'}",
            ]
        ),
        outputs=[
            _out("regime_classifier", "Validated OHLCV", "NIFTY50 + VIX data", _age(sentinel_ev["ts"]) if sentinel_ev else "never"),
            _out("all_pods",          "Quote stream",     "live LTP per symbol", "continuous"),
        ],
    )

    # ── Regime Classifier ──────────────────────────────────────────────────
    try:
        regime     = RegimeClassifier.get().current
        regime_enabled = feature_toggles.is_enabled("regime_classifier")
        reg_status = "ok" if regime_enabled else "warn"
        # REGIME_CHANGE is only published when the regime actually flips, which
        # can be hours apart — using that as the "last activity" timestamp made
        # this node look permanently inactive even though it recomputes (and
        # updates .current) every 30 minutes regardless of whether anything
        # changed. Use the snapshot's own timestamp instead.
        reg_age    = _age(regime.timestamp.replace(tzinfo=timezone.utc).timestamp()) if regime.timestamp else "not yet"

        regime_node = _node(
            id="regime_classifier", label="Regime Classifier" if regime_enabled else "Regime Classifier (PAUSED)", ntype="processor",
            status=reg_status,
            inputs=[
                _inp("yahoo_finance", "NIFTY50 60d daily OHLCV", "close prices for ADX calc",                                   "on 30min interval"),
                _inp("yahoo_finance", "INDIAVIX 5d daily",        f"VIX = {regime.vix:.1f}" if regime.vix else "VIX pending", "on 30min interval"),
            ],
            state=_state(
                f"Classified: {regime.trend.value.upper()}" if regime_enabled else "PAUSED — manually disabled",
                [
                    *([] if regime_enabled else ["Toggled off from the Flow page — holding last classification, not recomputing."]),
                    f"Trend (ADX):   {regime.trend.value}",
                    f"Risk posture:  {regime.risk_posture.value}",
                    f"Volatility:    {regime.volatility.value}",
                    f"Bias:          {regime.bias.value}",
                    f"VIX:           {regime.vix:.2f}" if regime.vix else "VIX:           pending",
                    f"Confidence:    {regime.confidence:.0%}",
                    f"Recalc every:  30 minutes",
                ]
            ),
            outputs=[
                _out("pod_supervisor", "RegimeSnapshot",   f"{regime.trend.value} / {regime.risk_posture.value} / VIX {regime.vix:.1f}" if regime.vix else f"{regime.trend.value} / {regime.risk_posture.value}", reg_age),
                _out("message_bus",   "REGIME_CHANGE msg", f"published to all subscribers", reg_age),
            ],
        )
    except Exception as e:
        regime_node = _node(
            id="regime_classifier", label="Regime Classifier", ntype="processor",
            status="error",
            inputs=[], state=_state("Error", [str(e)]), outputs=[],
        )

    # ── LLM Gateway ───────────────────────────────────────────────────────
    try:
        provider  = getattr(_llm_gateway, "_provider", None) if _llm_gateway else None
        pname     = getattr(provider, "name", "unknown") if provider else "not set"
        llm_ev    = ft.last_of_type("llm_response") or ft.last_of_type("llm_request")
        llm_age   = _age(llm_ev["ts"]) if llm_ev else "no calls yet"

        llm_enabled = feature_toggles.is_enabled("llm_gateway")
        llm_node = _node(
            id="llm_gateway", label="LLM Gateway" if llm_enabled else "LLM Gateway (PAUSED)", ntype="processor",
            status="ok" if (_llm_gateway and llm_enabled) else "warn",
            inputs=[
                _inp("momentum_pod",      "Analysis request", "ticker list + regime context", llm_age),
                _inp("breakout_pod",      "Analysis request", "breakout candidates",          llm_age),
                _inp("mean_reversion_pod","Analysis request", "mean-rev candidates",          llm_age),
                _inp("event_pod",         "Analysis request", "news event + symbol",          llm_age),
                _inp("news",              "Analysis request", "headline severity/sentiment",  llm_age),
            ],
            state=_state(
                f"Provider: {pname}",
                [
                    f"Provider:    {pname}",
                    f"Model:       gpt-4.1-mini (Azure OpenAI)" if pname != "mock" else "Model: mock (no API call)",
                    f"Tiers:       fast / standard / reasoning / deep",
                    f"Last call:   {llm_age}",
                ]
            ),
            outputs=[
                _out("momentum_pod",       "Trade signal + confidence", "BUY/SELL + reasoning", llm_age),
                _out("breakout_pod",       "Trade signal + confidence", "BUY/SELL + reasoning", llm_age),
                _out("mean_reversion_pod", "Trade signal + confidence", "BUY/SELL + reasoning", llm_age),
            ],
        )
    except Exception as e:
        llm_node = _node(
            id="llm_gateway", label="LLM Gateway", ntype="processor",
            status="error", inputs=[], state=_state("Error", [str(e)]), outputs=[],
        )

    # ── Pod Supervisor ────────────────────────────────────────────────────
    pod_count = len(_pod_supervisor.pods) if _pod_supervisor else 0
    sup_ev    = ft.last_from("pod_supervisor")
    sup_age   = _age(sup_ev["ts"]) if sup_ev else "no events yet"

    pod_supervisor_enabled = feature_toggles.is_enabled("pod_supervisor")
    supervisor_node = _node(
        id="pod_supervisor", label="Pod Supervisor" if pod_supervisor_enabled else "Pod Supervisor (PAUSED)", ntype="orchestrator",
        status="ok" if (_pod_supervisor and pod_supervisor_enabled) else "warn",
        inputs=[
            _inp("regime_classifier", "RegimeSnapshot", "current regime + risk posture", sup_age),
            _inp("circuit_breaker",   "Risk alerts",    "halt / resume signals",         sup_age),
        ],
        state=_state(
            f"Managing {pod_count} pods",
            [
                f"Active pods:    {pod_count}",
                f"Circuit state:  normal",
                f"Lifecycle:      SANDBOX → PROBATION → LIVE → REVIEW → KILLED",
                f"Last action:    {sup_age}",
            ]
        ),
        outputs=[
            _out("momentum_pod",       "Capital allocation + regime",  "₹1,00,000 allocated", "on regime change"),
            _out("breakout_pod",       "Capital allocation + regime",  "₹1,00,000 allocated", "on regime change"),
            _out("mean_reversion_pod", "Capital allocation + regime",  "₹1,00,000 allocated", "on regime change"),
            _out("event_pod",          "Capital allocation + regime",  "₹1,00,000 allocated", "on regime change"),
        ],
    )

    # ── Individual Pods ───────────────────────────────────────────────────
    pod_nodes: list[dict] = []
    if _pod_supervisor:
        for pod in _pod_supervisor.pods.values():
            try:
                m    = pod.get_metrics()
                pid  = pod.config.pod_id
                pname = pod.config.pod_name
                state = pod.config.state.value
                strat = pod.config.strategy

                pod_ev  = ft.last_from(pid)
                pod_age = _age(pod_ev["ts"]) if pod_ev else "no events yet"

                cap_alloc = float(pod.config.capital_budget)

                is_paused = getattr(pod, "_is_paused", False)
                pod_node = _node(
                    id=f"pod_{pid}", label=pname, ntype="pod",
                    status="warn" if (is_paused or state in ("review", "killed")) else "ok",
                    inputs=[
                        _inp("pod_supervisor", "Capital allocation",     f"₹{cap_alloc:,.0f}",           "on start"),
                        _inp("data_sentinel",  "Quote stream",           "LTP per watchlist symbol",     "continuous"),
                        _inp("llm_gateway",    "AI signal + confidence", "trade decision + reasoning",   pod_age),
                        _inp("regime_classifier", "Regime context",      f"{pod.config.strategy} mode",  pod_age),
                    ],
                    state=_state(
                        f"{strat} strategy — {state.upper()}{' (PAUSED)' if is_paused else ''}",
                        [
                            *(["Paused — not processing quotes or signals."] if is_paused else []),
                            f"Pod state:      {state}",
                            f"Strategy:       {strat}",
                            f"Total signals:  {m.total_trades}",
                            f"Win rate:       {m.win_rate:.0%}",
                            f"Total P&L:      ₹{float(m.total_pnl):,.2f}",
                            f"Last activity:  {pod_age}",
                        ]
                    ),
                    outputs=[
                        _out("capital_tracker", "Order request",    f"{m.total_trades} signals → capital check", pod_age),
                        _out("message_bus",     "SIGNAL_GENERATED", "published for feedback engine",             pod_age),
                    ],
                )
                pod_node["pod_id"] = pid
                pod_node["is_paused"] = is_paused
                pod_nodes.append(pod_node)
            except Exception:
                pod_nodes.append(_node(
                    id=f"pod_{pod.config.pod_id}", label=pod.config.pod_name, ntype="pod",
                    status="warn", inputs=[], state=_state("Metrics unavailable", []), outputs=[],
                ))

    # ── Capital Tracker ───────────────────────────────────────────────────
    try:
        snap     = await CapitalTracker.get().snapshot()
        cap_ev   = ft.last_from("capital_tracker")
        cap_age  = _age(cap_ev["ts"]) if cap_ev else "no events yet"

        pillars = snap.pillar_allocations if hasattr(snap, "pillar_allocations") else {}
        pillar_lines = [
            f"{k.replace('_',' ')}: ₹{float(v.allocated):,.0f}"
            for k, v in pillars.items()
        ]

        capital_tracker_enabled = feature_toggles.is_enabled("capital_tracker")
        cap_node = _node(
            id="capital_tracker", label="Capital Tracker" if capital_tracker_enabled else "Capital Tracker (PAUSED)", ntype="processor",
            status="ok" if capital_tracker_enabled else "warn",
            inputs=[
                _inp("all_pods", "Order requests", "pre-trade risk check request", cap_age),
            ],
            state=_state(
                "Capital allocation",
                [
                    f"Total capital:  ₹{float(snap.total_capital):,.0f}",
                    f"Available:      ₹{float(snap.available_capital):,.0f}",
                    f"Daily P&L:      ₹{float(snap.daily_pnl):+,.2f}",
                    *pillar_lines,
                ]
            ),
            outputs=[
                _out("portfolio_guardian", "Pre-trade risk check", "order + account state", cap_age),
                _out("paper_broker",       "Approved orders",      "after guardian clears", cap_age),
            ],
        )
    except Exception as e:
        cap_node = _node(
            id="capital_tracker", label="Capital Tracker", ntype="processor",
            status="error", inputs=[], state=_state("Error", [str(e)]), outputs=[],
        )

    # ── Portfolio Guardian ────────────────────────────────────────────────
    guard_ev  = ft.last_from("portfolio_guardian")
    guard_age = _age(guard_ev["ts"]) if guard_ev else "no events yet"
    guardian_enabled = feature_toggles.is_enabled("portfolio_guardian")

    guardian_node = _node(
        id="portfolio_guardian", label="Portfolio Guardian" if guardian_enabled else "Portfolio Guardian (PAUSED)", ntype="processor",
        status="ok" if guardian_enabled else "warn",
        inputs=[
            _inp("capital_tracker", "Order + account state", "pre-trade risk data",   guard_age),
            _inp("regime_classifier","Regime context",        "risk posture for rules", guard_age),
        ],
        state=_state(
            "Risk rules active" if guardian_enabled else "PAUSED — manually disabled",
            [
                *([] if guardian_enabled else ["Toggled off from the Flow page — not syncing positions, marking to market, or checking exits."]),
                "Max single trade:  2% of capital",
                "Max drawdown:      10% daily",
                "Max open positions: configurable",
                "RISK_OFF mode:     reduce size 50%",
                f"Last check:        {guard_age}",
            ]
        ),
        outputs=[
            _out("paper_broker", "Cleared order", "approved + sized order", guard_age),
            _out("message_bus",  "BLOCKED_ORDER", "if risk rule violated",  guard_age),
        ],
    )

    # ── Long-Term Desk (Room 1 idea debate → Room 2 capital → Room 3 execution) ─
    # CommitteeChair.deliberate() is a direct method call, never published to the
    # bus — FlowTracker has no "room1.committee_chair" source, so chair_age was
    # always "no ideas debated yet" regardless of real activity, which is why the
    # Flow page showed this node as permanently inactive. Use the explainability
    # ledger's real decision timestamps instead.
    queue_size = _lt_desk._aggregator.queue_size() if _lt_desk else 0
    universe_n = len(_lt_desk._universe) if _lt_desk else 0

    from ...audit.explainability_ledger import ExplainabilityLedger
    chair_decisions = await ExplainabilityLedger.get().query(agent_id="room1.committee_chair", limit=20)
    approved_recent = [d for d in chair_decisions if d.get("decision") in ("approve", "conditional")][:2]
    rejected_recent = [d for d in chair_decisions if d.get("decision") == "reject"][:2]

    if chair_decisions:
        last_ts_str = chair_decisions[0]["event_ts"]
        last_ts = datetime.fromisoformat(last_ts_str).replace(tzinfo=timezone.utc).timestamp()
        chair_age = _age(last_ts)
    else:
        chair_age = "no ideas debated yet"

    def _reason_line(d: dict) -> str:
        sym = d.get("symbol", "?")
        txt = (d.get("reasoning") or "")[:100]
        return f"  {sym}: {txt}{'...' if len(d.get('reasoning') or '') > 100 else ''}"

    approved_lines = [_reason_line(d) for d in approved_recent] or ["  (none yet)"]
    rejected_lines = [_reason_line(d) for d in rejected_recent] or ["  (none yet)"]

    # Structured, full-text version (not the truncated single-line summaries
    # above) — rendered as its own dedicated, highlighted block on the Flow
    # page instead of being buried in the generic state-lines list, which is
    # apparently very easy to miss.
    reasoning_feed = [
        {
            "symbol":   d.get("symbol", "?"),
            "decision": d.get("decision", "?"),
            "reasoning": d.get("reasoning", ""),
            "ts":       d.get("event_ts", ""),
            # What actually happened as a result of this verdict — bought or
            # not, how much, at what target/stop. Without this, "approved" and
            # "actually executed" look identical, which was the exact gap
            # flagged: reasoning with no link to the real action taken.
            "outcome":  d.get("outcome") or "pending — Room 2/3 still deciding",
        }
        for d in chair_decisions[:6]
    ]

    lt_enabled = feature_toggles.is_enabled("long_term_desk")
    lt_desk_node = _node(
        id="lt_desk_room1", label="Long-Term Desk (Room 1)" if lt_enabled else "Long-Term Desk (PAUSED)",
        ntype="orchestrator",
        status="ok" if lt_enabled else "warn",
        inputs=[
            _inp("news",        "Sentiment signal",      "positive/negative → idea",  chair_age),
            _inp("yahoo_finance","Technical/fundamental", "volume, momentum, earnings", chair_age),
        ],
        state=_state(
            "Idea debate → capital sizing → execution" if lt_enabled else "PAUSED — manually disabled",
            [
                *([] if lt_enabled else ["Toggled off from the Flow page — not scanning or debating."]),
                f"Universe scanned:  {universe_n} symbols",
                f"Ideas queued:      {queue_size}",
                "Debate panel:      Scout, Bull, Bear, Devil's Advocate, Sector, Momentum",
                "Verdict by:        Committee Chair (LLM)",
                f"Last verdict:      {chair_age}",
                "Recently APPROVED — why we'd buy:",
                *approved_lines,
                "Recently REJECTED — why we wouldn't:",
                *rejected_lines,
            ]
        ),
        outputs=[
            _out("capital_tracker", "AllocationPlan",  "if idea approved/conditional", chair_age),
            _out("paper_broker",    "Execution order", "after Room 3 risk checks",     chair_age),
        ],
    )
    lt_desk_node["reasoning_feed"] = reasoning_feed

    # ── Paper Broker ──────────────────────────────────────────────────────
    try:
        if _broker_gateway:
            broker    = _broker_gateway._broker
            is_conn   = getattr(broker, "is_connected", False)
            positions = await broker.get_positions()
            balance   = await broker.get_balance()
            brk_ev    = ft.last_from("paper_broker")
            brk_age   = _age(brk_ev["ts"]) if brk_ev else "no orders yet"
            paper_broker_enabled = feature_toggles.is_enabled("paper_broker")

            broker_node = _node(
                id="paper_broker", label="Paper Broker" if paper_broker_enabled else "Paper Broker (PAUSED)", ntype="sink",
                status="ok" if (is_conn and paper_broker_enabled) else "warn",
                inputs=[
                    _inp("portfolio_guardian", "Cleared order", "sized + approved order", brk_age),
                ],
                state=_state(
                    "Simulated fills (paper mode)",
                    [
                        f"Connected:       {'yes' if is_conn else 'no'}",
                        f"Open positions:  {len(positions)}",
                        f"Slippage model:  5 bps market orders",
                        f"Commission:      ₹20 flat per order",
                        f"Total value:     ₹{float(balance.total):,.0f}",
                        f"Last fill:       {brk_age}",
                    ]
                ),
                outputs=[
                    _out("capital_tracker", "OrderResult (FILLED/REJECTED)", "fill price + qty", brk_age),
                    _out("message_bus",     "ORDER_FILLED",                   "triggers P&L update", brk_age),
                ],
            )
        else:
            broker_node = _node(
                id="paper_broker", label="Paper Broker", ntype="sink",
                status="warn", inputs=[], state=_state("Not initialised", []), outputs=[],
            )
    except Exception as e:
        broker_node = _node(
            id="paper_broker", label="Paper Broker", ntype="sink",
            status="error", inputs=[], state=_state("Error", [str(e)]), outputs=[],
        )

    # ── 5Paisa (standalone broker connection, not yet wired into execution) ─
    from ...shared.service_log import get_logs
    fp_logs   = get_logs(service="five_paisa", limit=5)
    fp_active = (_broker_gateway.broker_name == "five_paisa") if _broker_gateway else False
    fp_tested = len(fp_logs) > 0
    fp_status = "ok" if (fp_active or fp_tested) else "warn"
    fp_last   = fp_logs[0]["message"] if fp_logs else "never tested"
    fp_age    = _age(fp_logs[0]["ts"]) if fp_logs else "never"

    five_paisa_enabled = feature_toggles.is_enabled("five_paisa")
    five_paisa_node = _node(
        id="five_paisa", label="5Paisa" if five_paisa_enabled else "5Paisa (PAUSED)", ntype="source",
        status=fp_status if five_paisa_enabled else "warn",
        inputs=[
            _inp("broker_gateway", "Order requests", "if set as active broker", fp_age),
        ],
        state=_state(
            "Active execution broker" if fp_active else "Connected, not wired into execution",
            [
                f"Active broker:  {'YES' if fp_active else 'no — paper_broker is active'}",
                f"Last call:       {fp_last}",
                f"Last call age:   {fp_age}",
                "Login:           TOTP-based, session cached for the day",
                "To go live:      set broker.default = \"five_paisa\" in config.toml",
            ]
        ),
        outputs=[
            _out("capital_tracker", "OrderResult (FILLED/REJECTED)", "fill price + qty", fp_age),
        ],
    )

    # ── Feedback Engine node ──────────────────────────────────────────────
    fb_ev  = ft.last_from("trade_attribution_engine")
    fb_age = _age(fb_ev["ts"]) if fb_ev else "no events yet"
    feedback_enabled = feature_toggles.is_enabled("feedback")

    feedback_node = _node(
        id="feedback_engine", label="Feedback Engine" if feedback_enabled else "Feedback Engine (PAUSED)", ntype="processor",
        status="ok" if feedback_enabled else "warn",
        inputs=[
            _inp("message_bus",   "ORDER_FILLED events",       "trade results",             fb_age),
            _inp("message_bus",   "SIGNAL_GENERATED events",   "pod signals",               fb_age),
            _inp("regime_classifier","Regime at signal time",  "for regime-adjusted scoring", fb_age),
        ],
        state=_state(
            "Continuous learning" if feedback_enabled else "PAUSED — manually disabled",
            [
                *([] if feedback_enabled else ["Toggled off from the Flow page — closed trades pile up unattributed until resumed."]),
                "TradeAttributionEngine: links outcomes to signals",
                "AgentCalibrationEngine: adjusts LLM agent weights",
                "VoteWeightUpdater:      rebalances pod influence",
                "StrategyPerformanceAnalyzer: tracks win/loss by regime",
                f"Last event: {fb_age}",
            ]
        ),
        outputs=[
            _out("llm_gateway",    "Updated agent weights",  "calibrated per regime",    fb_age),
            _out("pod_supervisor", "Performance scores",     "for lifecycle decisions",   fb_age),
        ],
    )

    # ── Assemble nodes + edges ─────────────────────────────────────────────
    nodes = [
        yf_node,
        five_paisa_node,
        news_node,
        reddit_node,
        news_extractor_node,
        database_node,
        sentinel_node,
        regime_node,
        llm_node,
        supervisor_node,
        *pod_nodes,
        cap_node,
        guardian_node,
        lt_desk_node,
        broker_node,
        feedback_node,
    ]

    pod_ids = [p["id"] for p in pod_nodes]
    edges: list[dict] = [
        _edge("e1",  "yahoo_finance",     "data_sentinel",     "OHLCV + quotes"),
        _edge("e2",  "data_sentinel",     "regime_classifier", "validated OHLCV"),
        _edge("e3",  "regime_classifier", "pod_supervisor",    "RegimeSnapshot"),
        _edge("e4",  "llm_gateway",       "pod_supervisor",    "AI analysis"),
        _edge("e5",  "yahoo_finance",     "database",          "cache writes"),
        _edge("e6",  "news",              "portfolio_guardian","alerts on bad news"),
        _edge("e7",  "news",              "pod_event_pod",     "every headline → buy/sell decision"),
        _edge("e8",  "news",              "lt_desk_room1",      "sentiment → debated idea"),
        _edge("e9",  "reddit",            "portfolio_guardian", "alerts on bad sentiment"),
        _edge("e10", "reddit",            "pod_event_pod",      "post → buy/sell decision"),
        _edge("e11", "reddit",            "lt_desk_room1",      "sentiment → debated idea"),
        _edge("e12", "yahoo_finance",     "news_extractor",     "per-symbol news"),
        _edge("e13", "news",              "news_extractor",     "RSS headlines"),
        _edge("e14", "reddit",            "news_extractor",     "retail posts"),
        _edge("e15", "news_extractor",    "lt_desk_room1",       "consolidated reasoning (context only)"),
    ]
    for i, pid in enumerate(pod_ids, 10):
        edges += [
            _edge(f"e_sup_{i}", "pod_supervisor",  pid,               "capital + regime"),
            _edge(f"e_ds_{i}",  "data_sentinel",   pid,               "quote stream"),
            _edge(f"e_llm_{i}", "llm_gateway",     pid,               "trade signal"),
            _edge(f"e_cap_{i}", pid,               "capital_tracker", "order request"),
        ]
    edges += [
        _edge("e20", "capital_tracker",    "portfolio_guardian", "pre-trade check"),
        _edge("e21", "portfolio_guardian", "paper_broker",       "cleared order"),
        _edge("e22", "paper_broker",       "capital_tracker",    "OrderResult"),
        _edge("e23", "paper_broker",       "feedback_engine",    "ORDER_FILLED"),
        _edge("e24", "feedback_engine",    "llm_gateway",        "agent weights"),
        _edge("e25", "feedback_engine",    "pod_supervisor",     "performance scores"),
        _edge("e26", "lt_desk_room1",      "capital_tracker",    "AllocationPlan (Room 2)"),
        _edge("e27", "lt_desk_room1",      "paper_broker",       "execution order (Room 3)"),
    ]

    # Recent bus events for the timeline
    recent = [
        {"ts": e["ts"], "type": e["type"], "source": e["source"], "summary": e["payload"]}
        for e in ft.recent(30)
    ]

    return {
        "timestamp": now,
        "uptime_s":  uptime,
        "nodes":     nodes,
        "edges":     edges,
        "recent_events": recent,
    }


# ── Builders ──────────────────────────────────────────────────────────────────

def _node(id: str, label: str, ntype: str, status: str,
          inputs: list, state: dict, outputs: list) -> dict:
    return {
        "id":      id,
        "label":   label,
        "type":    ntype,
        "status":  status,
        "inputs":  inputs,
        "state":   state,
        "outputs": outputs,
    }

def _edge(id: str, source: str, target: str, label: str) -> dict:
    return {"id": id, "source": source, "target": target, "label": label}
