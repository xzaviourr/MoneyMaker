# MoneyMaker — Multi-Agent Algorithmic Trading Platform

AI-powered trading system for Indian stock markets (NSE/BSE). Multiple strategy agents run in parallel, each analysing the market independently. A regime classifier, risk guardian, and continuous feedback loop coordinate them.

> **Project status:** active prototype. Use `--demo` or paper mode for
> evaluation. This repository does not promise returns and is not financial
> advice.

---

## Quick Start

**Install (once):**
```bash
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e .          # installs from pyproject.toml
cd ui && npm install
```

**Copy and fill credentials:**
```bash
cp .env.example .env      # then add AZURE_OPENAI_API_KEY
```

**Run backend:**
```bash
python main.py --demo     # mock LLM, no credentials needed
python main.py --paper    # real Azure LLM + paper broker
```

**Run frontend** (separate terminal):
```bash
cd ui && npm run dev      # opens at http://localhost:5173
```

---

## Pages

| URL | What it shows |
|-----|---------------|
| `/` | Dashboard — regime, capital, live event stream |
| `/flow` | System Flow — data lineage graph, click any node for IN/OUT |
| `/portfolio` | Open positions + exit controls |
| `/pods` | Pod list — state, P&L, pause/kill controls |
| `/trades` | All trade history |
| `/reports` | P&L summary — daily/weekly/monthly, win rate, best/worst trade |
| `/decisions` | Agent decision log with full debate transcript |
| `/feedback` | Feedback engine — calibration scores, vote weights |
| `/logs` | Raw service log stream |

---

## Architecture

```
Yahoo Finance (market data)
       │
       ▼
 Data Sentinel (validates, publishes to bus)
       │
   ┌───┴────────────────────┐
   ▼                        ▼
Regime Classifier      [Quote stream → all pods]
(ADX + VIX → trend)
   │
   ▼
Pod Supervisor  ←──── LLM Gateway (Azure gpt-4.1-mini)
 ┌──────┬──────┬──────┐
 ▼      ▼      ▼      ▼
Mom  Break  MeanRev  Event     ← SANDBOX → PROBATION → LIVE
 └──────┴──────┴──────┘
        │
        ▼
 Portfolio Guardian (risk: 2% per trade, 10% max drawdown)
        │
        ▼
 Paper Broker / 5Paisa (fills)
        │
        ▼
 Feedback Engine (outcomes → agent weights → LLM calibration)
```

All components communicate through a **MessageBus** (async pub/sub, in-process).
Every message is recorded by **FlowTracker** and visible on the Flow page.

---

## Capital Allocation

Total: ₹10,00,000

| Pillar | % | Amount |
|--------|---|--------|
| Intraday pods | 40% | ₹4,00,000 |
| Long-term desk | 50% | ₹5,00,000 |
| Guardian reserve | 10% | ₹1,00,000 |

Each intraday pod gets ₹1,00,000 (configurable via `config.toml → pods.per_pod_budget`).

---

## Configuration

**`config.toml`** — all business logic (capital, thresholds, watchlists, LLM models).  
**`.env`** — secrets only (Azure key, 5Paisa credentials). Never commit this file.

Change which stocks each pod watches without touching code:
```toml
[watchlists]
momentum = ["RELIANCE", "TCS", "INFY", ...]
breakout  = ["NIFTY", "TATAMOTORS", ...]
mean_rev  = ["HDFCBANK", "ICICIBANK", ...]
```

---

## Project Structure

```
main.py                        ← entry point (--demo / --paper flags)
config.toml                    ← business config
.env                           ← secrets (never commit)

src/
  shared/          ← MessageBus, schemas, market data cache, config
  feeds/           ← Reddit + RSS news feeds
  audit/           ← ExplainabilityLedger (immutable decision log)
  foundation/      ← RegimeClassifier, DataSentinel
  llm/             ← LLMGateway (Azure OpenAI, mock provider)
  brokers/         ← PaperBroker, 5PaisaBroker, BrokerGateway
  pods/            ← BasePod + 4 strategies (momentum, breakout, mean_rev, event)
  supervisor/      ← PodSupervisor, CapitalTracker, CircuitBreaker, FirmCIO
  guardian/        ← PortfolioGuardian + sub-agents (position monitor, news watchdog…)
  long_term_desk/  ← Room 1/2/3 debate system for multi-day trades
  feedback/        ← Attribution, calibration, vote weights, performance analysis
  api/             ← FastAPI app, routes, WebSocket live feed

ui/src/
  pages/           ← Dashboard, Pods, Reports, Decisions, Feedback, Flow…
  components/      ← Shared UI (ErrorBoundary, Skeleton, DebateModal…)
  hooks/           ← useLiveFeed (WebSocket), useStore (zustand)
  lib/             ← api.ts (fetch wrapper), utils.ts

tests/             ← pytest suite (CapitalLedger, MessageBus, FlowTracker)
```

---

## Key Rules (for contributors)

1. **Never commit `.env`** — credentials stay in `.env` only
2. **Singleton access** — always `Component.get()`, never `Component()` for:  
   MessageBus, RegimeClassifier, CapitalTracker, CircuitBreaker, LLMGateway, BrokerGateway, FlowTracker, PortfolioGuardian
3. **`bus.subscribe()` is synchronous** — never `await` it
4. **Market data** — always via `market_data_cache.download()` / `get_quote()`, never `yf` directly
5. **Paper mode first** — all logic must work in `--paper` mode before going live

---

## Running Tests

```bash
pytest tests/ -v
```

For a dependency-free syntax check:

```bash
python -m compileall -q main.py src tests
```

The current automated tests focus on the capital ledger, in-process message
bus, and flow tracker. They do not validate broker execution, market-data
availability, strategy profitability, or the React UI.

---

## 5Paisa Integration (pending)

When the account is reactivated, add to `.env`:
```
FIVEPAISA_CLIENT_CODE=...
FIVEPAISA_PASSWORD=...
FIVEPAISA_TOTP_SECRET=...
```

`BrokerGateway.from_config(paper=False)` switches automatically when these are set.

---

## Safety and limitations

- Start in demo or paper mode. Live orders can lose money.
- Never commit `.env`, broker credentials, TOTP secrets, or cloud keys.
- Yahoo Finance, Reddit, RSS, Azure OpenAI, Redis, PostgreSQL, and broker
  integrations can fail or change independently.
- Strategy outputs and LLM responses are probabilistic; risk controls reduce
  exposure but cannot eliminate market, model, execution, or software risk.
- Historical and paper results are not evidence of future performance.
- Review exchange rules, broker terms, taxes, and local regulations before any
  real-money use.

## License

No repository-wide license is currently granted. The history includes work
from multiple contributors, so copyright remains with the respective authors
unless they agree on a license.
