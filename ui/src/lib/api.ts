import { useStore } from '../hooks/useStore'

const API_KEY = (import.meta.env.VITE_API_KEY as string | undefined) ?? ''
const DEFAULT_PORT = 8000

// The default portfolio (port 8000) keeps using the relative, Vite-proxied
// '/api' path — byte-for-byte the same request it always made. Any other
// portfolio calls its backend directly by port; CORS on the backend is
// already wide open, so no proxy config needs to know about it in advance.
function base(): string {
  const { portfolios, selectedPortfolioId } = useStore.getState()
  const port = portfolios.find(p => p.id === selectedPortfolioId)?.port ?? DEFAULT_PORT
  return port === DEFAULT_PORT ? '/api' : `http://localhost:${port}/api`
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
