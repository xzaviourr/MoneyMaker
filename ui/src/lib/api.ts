const BASE = '/api'

export async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
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
