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
Pod Supervisor  ←──── LLM Gateway (gpt-4.1-mini via Azure)
 ┌──────┬──────┐
 ▼      ▼      ▼
Mom   Break  MeanRev      ← strategy pods (SANDBOX → PROBATION → LIVE)
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
```

All components communicate through a **MessageBus** (async pub/sub, in-process).
Every message is tracked by **FlowTracker** and visible on the Flow dashboard.

---

## Tech Stack

| Layer       | Technology                                      |
|-------------|--------------------------------------------------|
| Backend     | Python 3.11, FastAPI, uvicorn, asyncio           |
| AI / LLM    | Azure OpenAI — gpt-4.1-mini (all tiers)          |
| Market data | Yahoo Finance (yfinance) + SQLite cache          |
| Frontend    | React 18, TypeScript, Vite, Tailwind CSS         |
| UI graphs   | @xyflow/react (React Flow)                       |
| State/fetch | @tanstack/react-query, zustand                   |
| Broker      | Paper broker (simulation) → 5Paisa (live)        |
| Config      | config.toml + .env (never commit .env)           |

---

## Key Files

```
main.py                          ← system entry point (--demo / --paper flags)
config.toml                      ← all system config (capital, LLM tiers, etc.)
.env                             ← Azure credentials (NEVER commit this)

src/
  shared/
    schemas.py                   ← all domain types (Order, Quote, RegimeSnapshot…)
    message_bus.py               ← async pub/sub backbone
    flow_tracker.py              ← records every bus message for the dashboard
    market_data_cache.py         ← SQLite-backed Yahoo Finance cache (24h TTL)
    config.py                    ← loads config.toml + pydantic settings

  foundation/
    regime_classifier.py         ← ADX + VIX → TRENDING/CHOPPY/MEAN_REVERTING
    data_sentinel.py             ← validates + publishes market data to bus

  llm/
    llm_gateway.py               ← routes LLM calls by tier (fast/standard/deep)
    azure_openai_provider.py     ← Azure OpenAI implementation
    mock_provider.py             ← offline mock (returns canned JSON, no API call)

  brokers/
    paper_broker.py              ← simulated fills with slippage + commission
    broker_gateway.py            ← singleton wrapper, switches paper ↔ 5Paisa

  pods/
    momentum_pod/                ← trend-following strategy
    breakout_pod/                ← breakout detection strategy
    mean_reversion_pod/          ← mean-reversion strategy
    base_pod.py                  ← shared pod lifecycle (SANDBOX→PROBATION→LIVE)

  supervisor/
    pod_supervisor.py            ← manages pod lifecycle + capital allocation
    capital_tracker.py           ← tracks deployed capital per pillar
    circuit_breaker.py           ← halts all trading on drawdown breach

  guardian/
    portfolio_guardian.py        ← pre-trade risk rules (size, drawdown, regime)

  feedback/
    trade_attribution_engine.py  ← links trade outcomes to the signals that caused them
    agent_calibration_engine.py  ← adjusts per-agent LLM weights based on accuracy
    vote_weight_updater.py       ← rebalances pod influence
    strategy_performance_analyzer.py ← tracks win/loss by strategy + regime

  api/
    main.py                      ← FastAPI app, registers all routers
    routes/
      portfolio.py               ← /portfolio/status, /snapshot, /positions
      pods.py                    ← /pods, /pods/{id}, /pods/{id}/command
      system.py                  ← /system/graph (data lineage for Flow dashboard)
      feedback.py                ← /feedback/summary, /feedback/review
    websocket/
      live_feed.py               ← ws://localhost:8000/ws/live (real-time events)

ui/src/
  App.tsx                        ← nav bar + routes
  pages/
    Dashboard.tsx                ← regime card, capital, live events
    SystemFlow.tsx               ← React Flow data lineage graph (the main debug UI)
    Pods.tsx                     ← pod list + lifecycle controls
    Positions.tsx                ← open positions
    Decisions.tsx                ← agent decisions log
    Feedback.tsx                 ← feedback + calibration summary
  hooks/
    useLiveFeed.ts               ← WebSocket connection to backend
    useStore.ts                  ← zustand store for live events
  lib/
    api.ts                       ← fetch wrapper (base URL = http://localhost:8000)
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
python main.py --paper     # real Azure LLM + paper broker
```

**Start frontend** (in /ui):
```bash
npm run dev                # opens at http://localhost:3000
```

**Pages:**
- `/`      Dashboard — regime, capital, live events
- `/flow`  System Flow — full data-lineage graph (click any node to see IN/OUT)
- `/pods`  Pod list + lifecycle commands
- `/positions` Open positions
- `/decisions` Agent decision log
- `/feedback` Feedback and calibration

---

## Current Status

### Done
- [x] MessageBus (async pub/sub backbone)
- [x] RegimeClassifier (ADX + VIX → regime classification every 30 min)
- [x] DataSentinel (market data validation)
- [x] LLM Gateway (Azure OpenAI gpt-4.1-mini, all 4 tiers)
- [x] MockLLMProvider (offline demo mode, no API key needed)
- [x] PaperBroker (simulated fills with slippage + commission)
- [x] 3 strategy pods: Momentum, Breakout, Mean Reversion
- [x] PodSupervisor (lifecycle SANDBOX→PROBATION→LIVE→REVIEW→KILLED)
- [x] CapitalTracker (₹10,00,000 across 3 pillars)
- [x] PortfolioGuardian (risk rules: 2% per trade, 10% drawdown)
- [x] Feedback engine (attribution, calibration, vote weights)
- [x] FastAPI backend with WebSocket live feed
- [x] React frontend — Dashboard, Pods, Positions, Decisions, Feedback
- [x] SQLite market data cache (24h TTL, auto backoff on 401)
- [x] FlowTracker (records every bus message for data lineage)
- [x] System Flow graph dashboard (React Flow, full data lineage with IN/STATE/OUT per node)

### Pending / TODO
- [ ] 5Paisa broker integration (account reactivating — needs Client Code, Password, TOTP Secret)
- [ ] Watchlist configuration per pod (which stocks each pod trades)
- [ ] Sentiment data feed (Reddit, Twitter, news APIs — future layer)
- [ ] Live backtesting runner (replay historical data through all agents)
- [ ] Pod promotion logic (auto-promote from SANDBOX to PROBATION after N profitable days)
- [ ] Alert system (Telegram/email on drawdown breach or unusual signals)
- [ ] Multi-desk support (intraday desk is built; long-term desk skeleton exists)
- [ ] Production deployment (Docker + cloud hosting)

---

## Important Rules

1. **Never commit .env** — Azure credentials stay in .env only (it is in .gitignore)
2. **Never hardcode credentials** in any .py or .ts file
3. **Paper mode first** — all live trading logic must work in paper mode before going live
4. **Singleton pattern** — always use `Component.get()` not `Component()` for:
   - MessageBus, RegimeClassifier, CapitalTracker, CircuitBreaker, LLMGateway, BrokerGateway
5. **Bus subscribe is sync** — `bus.subscribe()` is NOT async, never `await` it
6. **FlowTracker must stay wired** — `bus.subscribe_all(flow_tracker.handle)` in main.py startup
7. **Market data always via cache** — use `market_data_cache.download()` not `yf.download()` directly

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
- **Model**: gpt-4.1-mini (all tiers: fast / standard / reasoning / deep)
- **API Version**: 2024-12-01-preview
- **Key**: in .env as `AZURE_OPENAI_API_KEY`
