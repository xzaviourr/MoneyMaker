import { useStore } from '../hooks/useStore'

const API_KEY = (import.meta.env.VITE_API_KEY as string | undefined) ?? ''
const DEFAULT_PORT = 8000

// Always builds an absolute URL off the page's own hostname (matches
// useLiveFeed.ts's getWsUrl) — a hardcoded 'localhost' only works when
// browsing from the same machine the backend runs on, and breaks the moment
// the dashboard is opened remotely (VM's public IP, a phone, etc.). CORS on
// the backend is already wide open, so calling the port directly needs no
// proxy config either in dev or production. No '/api' segment here — the
// FastAPI routes are mounted at the root (/portfolio, /pods, ...), not under
// /api; that prefix only ever existed as a Vite dev-proxy rewrite rule.
function base(): string {
  const { portfolios, selectedPortfolioId } = useStore.getState()
  const port = portfolios.find(p => p.id === selectedPortfolioId)?.port ?? DEFAULT_PORT
  return `http://${window.location.hostname}:${port}`
}

function authHeaders(): Record<string, string> {
  return API_KEY ? { 'X-Api-Key': API_KEY } : {}
}

export async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${base()}${path}`, { headers: authHeaders() })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${base()}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export interface CapitalSnapshot {
  total_capital:     string
  available_capital: string
  deployed_capital:  string
  daily_pnl:         string
  pillar_allocations: Record<string, { pillar: string; allocated: string; deployed: string; available: string; pnl: string }>
}

export interface Position {
  id:                 string
  symbol:             string
  exchange:           string
  side:               string
  quantity:           number
  average_price:      string
  current_price:      string | null
  unrealized_pnl:     string
  unrealized_pnl_pct: number | null
  stop_loss:          string | null
  take_profit:        string | null
  max_hold_until:     string | null
  source_pod:         string | null
  source_desk:        string | null
  strategy:           string | null
  opened_at:          string | null
}

export interface PodInfo {
  pod_id:         string
  name:           string
  state:          string
  capital_budget: string
  metrics:        Record<string, unknown>
}

export interface QueueItem {
  symbol:                    string
  direction:                 string
  conviction_score:          number
  supporting_strategies:     string[]
  contradicting_strategies:  string[]
  queued_at:                 string
  expires_at:                string | null
}

export interface IntradayPodStatus {
  pod_id:         string
  name:           string
  state:          string
  watchlist:      string[]
  open_positions: number
  trades_today:   number
  last_updated:   string
}

export interface QueueResponse {
  long_term_queue_size: number
  long_term_queue:      QueueItem[]
  intraday_pods:        IntradayPodStatus[]
}

export interface Trade {
  trade_id:    string
  symbol:      string
  exchange:    string
  side:        string
  quantity:    number
  price:       number
  entry_price: number | null
  entry_time:  string | null
  pnl:         number
  charges?:    number   // absent on trades recorded before the Zerodha-accurate cost model
  tax?:        number
  net_pnl?:    number
  slippage:    number
  source_pod:  string | null
  source_desk: string | null
  strategy:    string | null
  timestamp:   string
}

export interface Decision {
  event_ts:  string
  agent_id:  string
  symbol:    string | null
  decision:  string
  reasoning: string
  outcome:   string | null
  inputs:    string | null
  outputs:   string | null
}

export interface RejectedTrackingRow {
  symbol:           string
  rejected_at:      string
  rejection_price:  number
  rejection_reason: string | null
  room:             string | null
  last_checked_at:  string | null
  last_price:       number | null
  pct_change:       number | null
  still_tracking:   number
}

export interface RejectedTrackingResponse {
  rows: RejectedTrackingRow[]
  summary: {
    checked:    number
    profitable: number
    hit_rate:   number | null
    by_room:    { room: string; total: number; profitable: number; hit_rate: number }[]
  }
}

export interface FeedbackSummary {
  strategies:    unknown[]
  agent_weights: Record<string, unknown>
  weakest:       string[]
}

export interface ServiceLog {
  id:      number
  service: string
  level:   string
  message: string
  details: Record<string, unknown> | null
  ts:      number
}

export interface UserIdea {
  id:                 number
  symbol:             string
  note:               string | null
  submitted_at:       string
  status:             'pending' | 'debated' | 'executed' | 'failed'
  verdict_approved:   number | null   // sqlite 0/1
  verdict_reasoning:  string | null
  bull_case:          string | null
  bear_case:          string | null
  devil_lean:         string | null
  chair_conviction:   number | null
  risk_passed:        number | null   // sqlite 0/1
  risk_issues:        string | null   // JSON-encoded string[]
  estimated_qty:      number | null
  estimated_price:    number | null
  estimated_capital:  number | null
  debated_at:         string | null
  error:              string | null
  executed_at:        string | null
  executed_qty:       number | null
  executed_price:     number | null
  executed_order_id:  string | null
}
