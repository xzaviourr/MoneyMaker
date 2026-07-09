# MoneyMaker — AI-Powered Algorithmic Trading System

## Instructions for Claude

Read this entire file before doing anything. This project has specific patterns
that must be followed. Violating them breaks the system silently.

### How to behave in every session
- Never ask the user to explain the project — it is all here.
- Never ask "should I proceed?" — just do it.
- When the user says something short like "fix pods" or "add that", infer from
  context what they mean and do it. Do not ask for clarification unless there
  are two genuinely different ways to do it that have different trade-offs.
- Run both backend (`python main.py --paper`) and frontend (`cd ui && npm run dev`)
  when testing UI changes. Always type-check TypeScript with `npx tsc --noEmit`.
- Keep responses short. The user is busy. One sentence per update while working.
- Do NOT add comments to code unless something is genuinely non-obvious.
- Do NOT create extra files, extra abstractions, or "future-proof" code.
  Build exactly what was asked, nothing more.
- Update this CLAUDE.md file at the end of every session with current status,
  what was done, and what is pending. This is the memory across sessions.

### Context about the user
- The user (Karan) is a student working under an instructor who owns the Azure
  account and 5Paisa account. Karan cannot access the Azure portal himself.
- The user restarts Claude sessions often — each new session must read this file
  and immediately know the full state without asking any questions.
- The user gets frustrated when Claude acts like it doesn't know the project.
  Never ask what the project is, never ask what was built before — it is all here.
- The user speaks casually and sometimes via voice transcription — understand
  the intent even if the sentence is broken.
- The instructor's name is not known — refer to him as "sir" or "the instructor".
- NEVER be surprised by anything in this project — read this file, know the state,
  act like you have been here from day one.

### Critical patterns — get these wrong and the system breaks

1. **Singleton access** — always `Component.get()`, never `Component()`:
   `MessageBus`, `RegimeClassifier`, `CapitalTracker`, `CircuitBreaker`,
   `LLMGateway`, `BrokerGateway`, `FlowTracker`

2. **bus.subscribe() is synchronous** — never `await bus.subscribe(...)`.
   Only `bus.publish()` is async.

3. **Market data** — always via `market_data_cache.download()` / `get_quote()`.
   Never call `yf.download()` or `yf.Ticker()` directly anywhere in the codebase.

4. **Pod factory functions** — pods live in directories (momentum_pod/),
   not single files. Their `__init__.py` exports `make_<name>_pod(gateway)`.
   The strategy class constructor takes only `gateway`, not `(config, gateway)`.

5. **Credentials** — NEVER write Azure keys, passwords, or TOTP secrets in
   any .py or .ts file. They live in `.env` only. `.env` is in `.gitignore`.
   Never mention the instructor's credentials in any output.

6. **FlowTracker wiring** — `bus.subscribe_all(FlowTracker.get().handle)` must
   be called in main.py right after MessageBus.get(). Do not remove it.

7. **Decisions database** — lives at `data/explainability.db`, table is
   `decision_log` (NOT `decisions`). Managed by `src/audit/explainability_ledger.py`.

---

## What We Are Building

A multi-agent algorithmic trading platform for Indian stock markets (NSE/BSE).
The system uses multiple AI-powered "pods" (strategy agents) that independently
analyse the market, generate trade signals, and place orders — all supervised by
a regime classifier, a risk guardian, and a continuous feedback loop that
re-weights agents based on their track record.

**End goal**: a fully autonomous trading system that:
- Classifies the current market regime (trending / choppy / mean-reverting)
- Runs strategy pods matched to the regime
- Routes orders through risk checks before execution
- Learns from outcomes to improve agent weights over time
- Connects to 5Paisa for live NSE/BSE execution

---

## Architecture

```
Yahoo Finance (market data)
       │
       ▼
 Data Sentinel (validates data, publishes to bus)
       │
   ┌───┴────────────────────┐
   ▼                        ▼
Regime Classifier      [Quote stream to all pods]
   │
   ▼
Pod Supervisor  ←──── LLM Gateway (Azure OpenAI via .env)
 ┌──────┬──────┐
 ▼      ▼      ▼
Mom   Break  MeanRev      ← intraday strategy pods (SANDBOX → PROBATION → LIVE)
 └──────┴──────┘
        │
        ▼
 Capital Tracker
        │
        ▼
 Portfolio Guardian (risk rules: max 2% per trade, 10% max drawdown)
        │
        ▼
 Paper Broker / 5Paisa (fills orders)
        │
        ▼
 Feedback Engine (links outcomes → agent weights → LLM calibration)

Long-Term Desk (runs in parallel — separate from intraday pods):
  Room 1 — Idea Generation:
    CatalystHunter, OpportunityScout, BullAdvocate, BearAdvocate,
    DevilsAdvocate, SectorSpecialist, MomentumAnalyst
  Room 2 — Capital Allocation:
    PortfolioCartographer, PositionSizer, AllocationChair,
    LiquidationStrategist, OpportunityCostAnalyst, CostBasisAccountant
  Room 3 — Execution:
    RiskGatekeeper, MarketTimer, TailRiskSentinel, ExecutionTrader
    PostTradeAuditor (logs every execution decision)
```

All components communicate through a **MessageBus** (async pub/sub, in-process).
Every message is tracked by **FlowTracker** and visible on the Flow dashboard.

---

## Tech Stack

| Layer       | Technology                                       |
|-------------|---------------------------------------------------|
| Backend     | Python 3.11, FastAPI, uvicorn, asyncio            |
| AI / LLM    | Azure OpenAI (deployment name from Azure portal)  |
| Market data | Yahoo Finance (yfinance) + SQLite cache           |
| Frontend    | React 18, TypeScript, Vite, Tailwind CSS          |
| UI graphs   | @xyflow/react (React Flow)                        |
| State/fetch | @tanstack/react-query, zustand                    |
| Broker      | Paper broker (simulation) → 5Paisa (live)         |
| Config      | config.toml + .env (never commit .env)            |
| DB          | SQLite — moneymaker.db (main), explainability.db  |

---

## Key Files

```
main.py                          ← system entry point (--demo / --paper flags)
config.toml                      ← all system config (capital, LLM tiers, etc.)
.env                             ← Azure credentials (NEVER commit this)
data/explainability.db           ← SQLite: table=decision_log (agent decisions)
data/paper_broker_state.json     ← paper broker trade state
data/attributed_trades.json      ← trade attribution records

src/
  shared/
    schemas.py                   ← all domain types (Order, Quote, RegimeSnapshot…)
    message_bus.py               ← async pub/sub backbone
    flow_tracker.py              ← records every bus message for the dashboard
    market_data_cache.py         ← SQLite-backed Yahoo Finance cache (24h TTL)
    config.py                    ← loads config.toml + pydantic settings
    service_log.py               ← structured logging helpers
    market_hours.py              ← NSE market hours utilities
    feature_toggles.py           ← feature flags
    trade_cost_estimator.py      ← brokerage cost calc

  audit/
    explainability_ledger.py     ← writes/queries decision_log table in explainability.db
    strategy_memory.py           ← strategy memory store

  feeds/
    reddit_feed.py               ← Reddit sentiment feed (future)
    rss_news.py                  ← RSS news feed (future)

  foundation/
    regime_classifier.py         ← ADX + VIX → TRENDING/CHOPPY/MEAN_REVERTING
    data_sentinel.py             ← validates + publishes market data to bus

  llm/
    llm_gateway.py               ← routes LLM calls by tier (fast/standard/deep)
    mock_provider.py             ← offline mock — per-symbol variation via MD5 seed
    azure_openai/
      provider.py                ← Azure OpenAI implementation
      deployment_map.py          ← maps tier → deployment name from config.toml
    tiers.py                     ← tier configs (cost, tokens, temperature)
    usage_tracker.py             ← tracks token usage + cost per agent

  brokers/
    paper_broker.py              ← simulated fills with slippage + commission
    broker_gateway.py            ← singleton wrapper, switches paper ↔ 5Paisa
    five_paisa/
      broker.py, auth.py, stream.py, symbol_mapper.py  ← 5Paisa integration (pending creds)
    zerodha/                     ← Zerodha stub (future)

  pods/
    momentum_pod/                ← trend-following intraday strategy
    breakout_pod/                ← breakout detection intraday strategy
    mean_reversion_pod/          ← mean-reversion intraday strategy
    scalp_pod/                   ← scalping strategy (built, not wired in)
    event_pod/                   ← event-driven strategy (built, not wired in)
    base_pod.py                  ← shared pod lifecycle (SANDBOX→PROBATION→LIVE)

  supervisor/
    pod_supervisor.py            ← manages pod lifecycle + capital allocation
    capital_tracker.py           ← tracks deployed capital per pillar
    circuit_breaker.py           ← halts all trading on drawdown breach
    firm_cio.py                  ← firm-level CIO oversight
    alpha_decay_monitor.py       ← detects when a strategy's edge is decaying

  guardian/
    portfolio_guardian.py        ← pre-trade risk rules (size, drawdown, regime)
    news_watchdog.py             ← monitors news for risk events
    position_monitor.py          ← monitors open positions for stop-loss
    correlation_watchdog.py      ← detects correlated position risk
    macro_shift_detector.py      ← detects macro regime shifts
    earnings_calendar_guard.py   ← blocks trades near earnings

  long_term_desk/
    long_term_desk.py            ← orchestrator, scans every 5 min
    signal_aggregator.py         ← aggregates signals from all strategies
    strategies/
      catalyst_hunter.py         ← finds catalyst-driven opportunities
      momentum_surf.py, trend_following.py, mean_reversion.py
      breakout.py, stat_arb.py, fundamentals.py, earnings_alpha.py
      chart_pattern.py, volume_profile.py, macro_regime.py
      insider_flow.py, sentiment.py, order_flow.py, short_interest.py
    room1_idea/
      opportunity_scout.py       ← generates initial idea thesis
      bull_advocate.py           ← argues bullish case
      bear_advocate.py           ← argues bearish case
      devils_advocate.py         ← challenges both cases
      sector_specialist.py       ← sector-level context
      momentum_analyst.py        ← momentum read
      committee_chair.py         ← Room 1 chair, final idea vote
    room2_capital/
      portfolio_cartographer.py  ← maps portfolio exposure
      position_sizer.py          ← sizes the position
      allocation_chair.py        ← Room 2 chair, approves allocation
      liquidation_strategist.py, opportunity_cost_analyst.py, cost_basis_accountant.py
    room3_execution/
      risk_gatekeeper.py         ← final pre-trade risk check
      market_timer.py            ← entry/exit timing
      tail_risk_sentinel.py      ← black swan risk check
      execution_trader.py        ← places the order
      post_trade_auditor.py      ← logs every execution decision

  portfolio/
    portfolio_manager.py         ← portfolio-level management

  feedback/
    trade_attribution_engine.py  ← links trade outcomes to the signals that caused them
    agent_calibration_engine.py  ← adjusts per-agent LLM weights based on accuracy
    vote_weight_updater.py       ← rebalances pod influence
    strategy_performance_analyzer.py ← tracks win/loss by strategy + regime
    outcome_attribution_timer.py ← times attribution analysis after trade closes
    parameter_optimizer.py       ← optimises strategy parameters
    regime_adjusted_scorer.py    ← scores strategies per regime
    system_review_agent.py       ← periodic system-level review

  api/
    main.py                      ← FastAPI app, registers all routers
    middleware/auth.py, request_id.py
    routes/
      portfolio.py               ← /portfolio/status, /snapshot, /positions
      pods.py                    ← /pods, /pods/{id}, /pods/{id}/command
      system.py                  ← /system/graph (data lineage for Flow dashboard)
      feedback.py                ← /feedback/summary, /feedback/review
      decisions.py               ← /decisions (queries explainability_ledger)
      news.py                    ← /news
      logs.py                    ← /logs
      commands.py                ← /commands
    websocket/
      live_feed.py               ← ws://localhost:8000/ws/live (real-time events)

ui/src/
  App.tsx                        ← nav bar + routes + ThemeToggle + SymbolFocus
  main.tsx                       ← theme init (reads localStorage mm-theme before render)
  index.css                      ← Tailwind base + light-mode overrides (html:not(.dark))
  pages/
    Dashboard.tsx                ← regime card, capital, live events
    SystemFlow.tsx               ← React Flow data lineage graph
    Pods.tsx                     ← pod list + lifecycle controls
    Positions.tsx                ← open positions (symbols are clickable → global sync)
    Trades.tsx                   ← trade history (symbols are clickable → global sync)
    Decisions.tsx                ← agent decisions log (auto-filters by selectedSymbol)
    Feedback.tsx                 ← feedback + calibration summary
    PortfolioManager.tsx         ← portfolio manager page
    Reports.tsx                  ← reports page
    Logs.tsx                     ← system logs page
  components/
    DebateModal.tsx              ← debate viewer: DebateSummaryCard (confrontation view)
                                    + raw agent entries behind toggle
    ErrorBoundary.tsx            ← React error boundary
    Skeleton.tsx                 ← loading skeleton component
    NewsApprovalToast.tsx        ← news approval toast notification
  hooks/
    useLiveFeed.ts               ← WebSocket connection to backend
    useStore.ts                  ← zustand store: live events + selectedSymbol global state
  lib/
    api.ts                       ← fetch wrapper (base URL = http://localhost:8000)
    utils.ts                     ← cn() and other helpers
```

---

## How to Run

**Install once:**
```bash
pip install -r requirements.txt
cd ui && npm install
```

**Start backend** (in project root):
```bash
python main.py --demo      # mock LLM, no credentials needed
python main.py --paper     # real Azure LLM + paper broker — WORKS (gpt-4.1-mini confirmed live)
```

**Start frontend** (in /ui):
```bash
npm run dev                # opens at http://localhost:5173
```

**Pages:**
- `/`           Dashboard — regime, capital, live events
- `/flow`       System Flow — full data-lineage graph
- `/portfolio`  Portfolio Manager
- `/pods`       Pod list + lifecycle commands
- `/trades`     Trade history
- `/positions`  Open positions
- `/decisions`  Agent decision log
- `/feedback`   Feedback and calibration
- `/reports`    Reports
- `/logs`       System logs

---

## Current Status (last updated: 2026-07-08 evening)

### Azure OpenAI — WORKING
- Deployment: `gpt-4.1-mini` on resource `anshul-ai-foundary-resource`
- Verified live: HTTP 200 on 2026-07-08
- `python main.py --paper` starts cleanly and uses real GPT
- CLAUDE.md previously had a stale "CRITICAL BLOCKER" entry — that is now removed

---

### Done — Backend
- [x] MessageBus (async pub/sub backbone)
- [x] RegimeClassifier (ADX + VIX → TRENDING/CHOPPY/MEAN_REVERTING)
- [x] DataSentinel (market data validation + bus publishing)
- [x] LLM Gateway (Azure OpenAI, all 4 tiers: fast/standard/reasoning/deep)
- [x] MockLLMProvider — per-symbol varied responses using MD5 hash seed
- [x] PaperBroker (simulated fills with slippage + commission)
- [x] 3 intraday pods: Momentum, Breakout, Mean Reversion
- [x] 2 extra pods built but not wired: ScalpPod, EventPod
- [x] PodSupervisor (lifecycle SANDBOX→PROBATION→LIVE→REVIEW→KILLED)
- [x] CapitalTracker (₹10,00,000 across 3 pillars)
- [x] CircuitBreaker (halts at -2% intraday, -4% emergency)
- [x] PortfolioGuardian (risk rules: 2% per trade, 10% drawdown)
- [x] Long-Term Desk: 3 rooms, 14+ strategy scanners, 7 Room 1 agents,
      6 Room 2 agents, 4 Room 3 agents — full debate pipeline
- [x] Feedback engine: attribution, calibration, vote weights, regime scoring
- [x] FastAPI backend with WebSocket live feed
- [x] ExplainabilityLedger (audit trail, data/explainability.db, table=decision_log)
- [x] SQLite market data cache (24h TTL, auto backoff on 401)
- [x] FlowTracker (records every bus message for data lineage)
- [x] config.toml tuned: scan_interval_minutes=5, min_conviction_to_queue=0.35

### Done — Frontend (UI)
- [x] All 10 pages: Dashboard, Flow, Portfolio Manager, Pods, Trades, Positions,
      Decisions, Feedback, Reports, Logs
- [x] Light/dark theme toggle — button in nav bar, persists to localStorage as
      `mm-theme`. main.tsx initializes theme before React renders.
      index.css has `html:not(.dark)` overrides that remap dark Tailwind tokens.
- [x] Global symbol sync — clicking any symbol on Positions or Trades page sets
      `selectedSymbol` in Zustand store; nav bar shows a focused symbol pill with
      "Debate ↗" button. Decisions page auto-filters to that symbol.
- [x] DebateModal with DebateSummaryCard — confrontation view showing:
      Scout thesis → Bull vs Bear side-by-side → Devil's Advocate → Signals →
      Chair verdict. Raw individual agent entries are behind a "Show raw" toggle.
- [x] Skeleton loading components

### Pending — Needs Instructor Action
- [ ] 5Paisa broker integration (account reactivating — needs Client Code,
      Password, TOTP Secret — goes in .env only, never in code)

### Light Mode — COMPLETE (as of 2026-07-08)
index.css now has comprehensive `html:not(.dark)` overrides covering:
- All gray background/border/text tokens
- Colored badge backgrounds: yellow/red/green/blue/orange/purple -900 and opacity variants
- Colored badge text: all -300/-400 color variants mapped to dark readable equivalents
- Dark -950 panel tints (opacity variants across all colors)
- White-opacity borders/fills, opacity gray variants
- Reports hardcoded hex backgrounds, React Flow controls
- Offline banner colors, misc UI elements
If any page still looks bad in light mode, add an override to the bottom of index.css.

### Pending — Future Work
- [ ] Watchlist config per pod (which stocks each pod trades)
- [ ] Sentiment data feed (feeds/ dir exists, not connected to pipeline)
- [ ] Live backtesting runner (replay historical data through all agents)
- [ ] Pod auto-promotion (SANDBOX → PROBATION after N profitable days)
- [ ] Alert system (Telegram/email on drawdown breach or unusual signals)
- [ ] Production deployment (Docker + cloud hosting)
- [ ] ScalpPod and EventPod wiring into PodSupervisor

---

## Important Rules

1. **Never commit .env** — Azure credentials stay in .env only (it is in .gitignore)
2. **Never hardcode credentials** in any .py or .ts file
3. **Paper mode first** — all live trading logic must work in paper mode before going live
4. **Singleton pattern** — always use `Component.get()` not `Component()` for:
   `MessageBus`, `RegimeClassifier`, `CapitalTracker`, `CircuitBreaker`,
   `LLMGateway`, `BrokerGateway`, `FlowTracker`
5. **Bus subscribe is sync** — `bus.subscribe()` is NOT async, never `await` it
6. **FlowTracker must stay wired** — `bus.subscribe_all(flow_tracker.handle)` in main.py startup
7. **Market data always via cache** — use `market_data_cache.download()` not `yf.download()` directly
8. **Decisions DB** — table is `decision_log` in `data/explainability.db`
9. **Update this file** — at the end of every session update Current Status section

---

## 5Paisa Integration (Pending)

When the instructor's 5Paisa account is reactivated, we need:
- **Client Code** (account ID)
- **Password**
- **TOTP Secret** (for 2FA — generates a 6-digit code every 30 seconds)

These go into `.env` as:
```
FIVEPAISA_CLIENT_CODE=...
FIVEPAISA_PASSWORD=...
FIVEPAISA_TOTP_SECRET=...
```

The broker abstraction is already built. `BrokerGateway.from_config(paper=False)`
will automatically switch to 5Paisa when these env vars are present.

---

## Azure OpenAI Config

- **Endpoint**: https://anshul-ai-foundary-resource.cognitiveservices.azure.com/
- **Deployment name**: `gpt-4.1-mini` — CONFIRMED WORKING (verified 2026-07-08)
- **API Version**: 2024-12-01-preview
- **Key**: in .env as `AZURE_OPENAI_API_KEY`
- **Embedding model**: text-embedding-3-large (separate deployment, may also need fix)

---

## Session Log (most recent first)

### 2026-07-08 — Azure confirmed working, light mode complete

**Key correction:** Previous session's CLAUDE.md claimed Azure deployment `gpt-4.1-mini`
was broken with 404. Tested today — HTTP 200, deployment is live and working.
`python main.py --paper` works. CLAUDE.md was stale; now corrected.

**What was built this session:**
1. `ui/src/index.css` — Comprehensive light mode overrides: colored badge backgrounds
   (yellow/red/green/blue/orange/purple), badge text colors (-300/-400 variants),
   dark -950 panel tints, gray opacity variants, offline banner, misc elements.
   Light mode now works across all 10 pages.
2. CLAUDE.md — Removed stale Azure blocker, corrected deployment status, added
   rule to update CLAUDE.md after every major change.

**Stops/trailing stop fixes from this session:**
- Hard stop widened to 2.5% (was 1.5%) in momentum pod and config.toml
- Breakout pod ATR stop multiplier raised to 2.0× (was 1.5×)
- Trailing stop now requires 1% profit before activating (was immediate)
- Yahoo Finance get_quote() has 3-method fallback (was silently returning None)

**What to do first in next session:**
- `python main.py --paper` to start with real Azure GPT
- System should now be generating real AI decisions (not mock)

### 2026-07-08 (evening) — trade loss investigation and fixes

**Root cause of 95% loss rate diagnosed:**
1. **News replay on restart** — `_seen_articles` was in-memory only. Every restart replayed
   the same 4-5 old stories (Reliance-Meta AI deal, HDFC dollar bonds, RBI forex swap, Morgan
   Stanley ICICI call) as fresh news, triggering 5-10 new event pod orders per restart.
   These stories accumulated 139 entries in the DB driving 263 event pod trades.
2. **Flat ₹20 commission underestimated** — `trade_cost_estimator.py` used
   `min(₹20, 0.03%)` for brokerage but paper broker charges flat ₹20 always. On ₹7-10k
   positions that's 0.4-0.8% round-trip just in commission — more than most signal edge.
3. **Edge check was one-way only** — `trade_has_edge` compared expected edge against
   one-way cost; should compare against round-trip (entry + exit) cost.
4. **Small position sizes** — position sizes were often ₹7-10k (too small relative to
   commission). Would improve with larger allocations but not fixed here.

**What was fixed this session:**
1. `src/guardian/news_watchdog.py` — Added `_load_seen_articles()` / `_save_seen_articles()`.
   Seen article IDs now persisted to `data/news_seen_articles.json`. Pre-seeded with all 139
   currently cached stale articles so they never replay again.
2. `src/pods/base_pod.py` — Added `_orders_in_flight: set[str]`. Before placing any order,
   the symbol is added to this set and removed in `finally`. `_handle_signal` checks this set
   so no concurrent signal fires while an order is mid-flight.
3. `src/shared/trade_cost_estimator.py` — Changed brokerage from `min(₹20, 0.03%)` to flat
   `₹20` to match actual paper broker charge. Changed `trade_has_edge` to compare expected edge
   against `round_trip_breakeven = breakeven * 2` instead of one-way.
4. `src/pods/base_pod.py` — Changed edge multiplier from `conviction * 2` to `conviction * 3`
   (requires 1.5× safety margin over round-trip breakeven, not just barely covering it).
5. `ui/src/App.tsx` — OfflineBanner now shows `python main.py --paper` (was `--demo`).

**Paper broker state:**
- Balance: ₹413,336 (started ₹1,000,000) — 58.7% loss from stale-news bug + commission drag
- 1052 trades: 19 wins, 441 losses, 592 open/unrealized
- Do NOT reset yet — wait and see if fixes improve the rate going forward
- If user wants to reset: delete `data/paper_broker_state.json`, it will restart from ₹10,00,000

**What to do first in next session:**
- `python main.py --paper` — news won't replay, edge check is tighter
- Monitor Decisions page for event pod trade count — should drop significantly
- If still losing heavily, consider raising `min_conviction_to_queue` in config.toml

### 2026-07-08 (later) — timestamp fix, dark mode default, decisions UX

1. `ui/src/pages/Decisions.tsx` — Fixed UTC timestamp display: added `+Z` suffix so
   `new Date()` correctly interprets Python's `datetime.utcnow()` strings as UTC and
   converts to IST for display. Changed default tab from `'all'` to `'debate'` so the
   page shows one card per stock (committee chair only) instead of every agent entry.
2. `ui/src/components/DebateModal.tsx` — Same UTC timestamp fix.
3. `ui/src/pages/PortfolioManager.tsx` — Same UTC timestamp fix.
4. `ui/src/main.tsx` — Always default to dark mode on page load/reload. Previous code
   persisted light mode across reloads; now every reload starts dark.
5. `config.toml` — Replaced `NIFTY` with `ADANIENT` in breakout watchlist (NIFTY is
   an index, not a Yahoo Finance tradeable ticker).
6. `main.py` — Silenced `azure.identity` and `azure.core` loggers (Key Vault auth noise).

---

### 2026-07-07 — Full session summary

**Key discovery (now outdated):** Session claimed Azure was broken. It is not.

**What was built this session:**

**What was built this session:**

1. `main.py` — Added `_verify_azure()` — on `--paper` startup, makes a test call
   to Azure and calls `sys.exit(1)` with a clear printed message if deployment
   not found or key rejected. No more silent fallback to mock AI.

2. `main.py` — Removed silent MockLLMProvider fallback when Azure key missing in
   paper mode. Now crashes with a clear message instead.

3. `src/audit/explainability_ledger.py` — Added `mode` column to `decision_log`
   table. `ExplainabilityLedger.init(mode)` called at startup tags every record
   as `demo` or `paper`. All existing records defaulted to `demo` (correct — they
   were all mock AI). Query supports `mode=` filter.

4. `src/api/routes/decisions.py` — Added `?mode=demo|paper` query param.

5. `ui/src/pages/Decisions.tsx` — Added mode filter buttons (Paper/Demo/All),
   mode badge on each card, `autoComplete="off"` on symbol input.
   Default filter is `all` so old data is visible.

6. `ui/src/App.tsx` — ThemeToggle, SymbolFocus (global symbol pill in nav bar),
   DebateModal opens from nav pill.

7. `ui/src/hooks/useStore.ts` — `selectedSymbol` + `setSelectedSymbol` global state.

8. `ui/src/main.tsx` — Theme init from localStorage before React renders.

9. `ui/src/index.css` — Light mode CSS overrides (`html:not(.dark)` selectors).

10. `ui/src/pages/Positions.tsx`, `Trades.tsx` — Symbols are clickable buttons,
    sets global selectedSymbol.

11. `ui/src/pages/Decisions.tsx` — Auto-filters by selectedSymbol from store.

12. `ui/src/components/DebateModal.tsx` — DebateSummaryCard: confrontation view
    (Scout → Bull vs Bear → Devil → Signals → Chair verdict). Raw entries behind toggle.

13. `src/llm/mock_provider.py` — Rewrote to generate per-symbol varied responses
    using MD5 hash seed. No two stocks produce identical debates.

14. `config.toml` — `scan_interval_minutes=5` (was 60), `min_conviction_to_queue=0.35` (was 0.55).

15. `CLAUDE.md` — Added user context, session log, full file tree, blocker details.

**State of the database (`data/explainability.db`):**
- 8032 records, all tagged `demo`, date range 2026-06-19 to 2026-07-07
- All came from MockLLMProvider — zero real GPT decisions ever
- Table: `decision_log`, columns: id, event_ts, agent_id, symbol, decision,
  reasoning, inputs, outputs, outcome, mode

**What to do first in the next session:**
1. Ask if the instructor sent the Azure deployment name
2. If yes: update `config.toml [llm.deployments]` all 4 lines, run `python main.py --paper`
3. If no: run `python main.py --demo` to keep development going
4. Do NOT run `--paper` until Azure deployment name is confirmed — it will
   now crash with a clear error message (that's correct behaviour)
