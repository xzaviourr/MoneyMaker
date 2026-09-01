import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchJson, type CapitalSnapshot, type Decision } from '../lib/api'
import { fmtInr, fmt, cn } from '../lib/utils'
import { useStore } from '../hooks/useStore'
import BuySuggestionsButton from '../components/NewsApprovalToast'

function parseJsonSafe(raw: string | null): Record<string, unknown> | null {
  if (!raw) return null
  try { return JSON.parse(raw) } catch { return null }
}

function fmtTs(iso: string | null | undefined): string {
  if (!iso) return '—'
  const utc = iso.endsWith('Z') ? iso : iso + 'Z'
  return new Date(utc).toLocaleString('en-IN', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

function fmtTsTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const utc = iso.endsWith('Z') ? iso : iso + 'Z'
  return new Date(utc).toLocaleTimeString('en-IN', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  })
}

// Always shows the most recently completed news → decision → trade story as
// a checklist, instead of requiring you to catch a 2-second flash live.
function LatestTraceCard() {
  const selectedPortfolioId = useStore(s => s.selectedPortfolioId)
  const { data } = useQuery<Decision[]>({
    queryKey: ['latest-trace', selectedPortfolioId],
    queryFn:  () => fetchJson('/decisions/?limit=1'),
    refetchInterval: 4000,
  })
  const d = data?.[0]
  if (!d) {
    return (
      <div className="card">
        <div className="text-xs font-mono text-gray-400 mb-2">Latest Trace</div>
        <div className="text-gray-600 text-sm">No completed decisions yet.</div>
      </div>
    )
  }

  const outputs = parseJsonSafe(d.outputs)
  const qty   = outputs?.quantity
  const price = outputs?.fill_price
  const hasTraded = qty != null && price != null

  const Step = ({ done, label, detail }: { done: boolean; label: string; detail: string }) => (
    <div className="flex gap-2 items-start">
      <span className={done ? 'text-green-400' : 'text-gray-600'}>{done ? '✅' : '⬜'}</span>
      <div>
        <div className="text-xs font-semibold text-gray-300">{label}</div>
        <div className="text-xs text-gray-500">{detail}</div>
      </div>
    </div>
  )

  return (
    <div className="card flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="text-xs font-mono text-gray-400">Latest Trace — {d.symbol}</div>
        <span className="text-xs text-gray-600">{fmtTs(d.event_ts)}</span>
      </div>
      <Step done label="📰 News / Signal analysed"
        detail={d.reasoning?.slice(0, 140) || '—'} />
      <Step done label={`🤖 AI decision: ${d.decision.toUpperCase()}`}
        detail={`Agent: ${d.agent_id}`} />
      <Step done={hasTraded} label="💰 Trade executed"
        detail={hasTraded ? `${qty} shares @ ₹${price}` : 'Not executed (rejected / deferred / verdict only)'} />
    </div>
  )
}

function PillarCard({ name, data }: { name: string; data: { allocated: string; available: string; deployed: string; pnl: string } }) {
  const pnl  = parseFloat(data.pnl)
  const util = parseFloat(data.deployed) / (parseFloat(data.allocated) || 1) * 100
  return (
    <div className="card flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-mono text-gray-400 uppercase tracking-wider">{name}</span>
        <span className={cn('text-sm font-semibold', pnl >= 0 ? 'pnl-pos' : 'pnl-neg')}>
          {pnl >= 0 ? '+' : ''}{fmtInr(pnl)}
        </span>
      </div>
      <div className="text-xl font-semibold font-mono">{fmtInr(data.allocated)}</div>
      <div className="w-full bg-gray-800 rounded-full h-1.5">
        <div className="bg-brand-500 h-1.5 rounded-full" style={{ width: `${Math.min(util, 100)}%` }} />
      </div>
      <div className="flex justify-between text-xs text-gray-500">
        {/* "Deployed" in the ledger means budget handed to a pod to trade
            within, not "currently has an open position" — a pod can sit at
            100% budgeted with zero open trades, which isn't a bug, just a
            confusing label if left as "Deployed". */}
        <span>Budgeted {fmt(util, 1)}%</span>
        <span>Available {fmtInr(data.available)}</span>
      </div>
    </div>
  )
}

function RegimeCard({ status }: { status: any }) {
  const regime = status?.regime ?? 'loading...'
  const risk   = status?.risk_posture ?? '—'
  const vol    = status?.volatility ?? '—'
  const vix    = status?.vix

  const regimeColor =
    regime === 'trending'      ? 'text-green-400' :
    regime === 'mean_reverting' ? 'text-blue-400'  :
    regime === 'choppy'        ? 'text-yellow-400' : 'text-gray-400'

  return (
    <div className="card flex flex-wrap gap-6 text-sm">
      <div>
        <span className="text-gray-500">Market Regime </span>
        <span className={cn('font-mono font-semibold capitalize', regimeColor)}>
          {regime.replace('_', ' ')}
        </span>
      </div>
      <div>
        <span className="text-gray-500">Risk Posture </span>
        <span className="font-mono font-semibold capitalize">{risk.replace('_', ' ')}</span>
      </div>
      <div>
        <span className="text-gray-500">Volatility </span>
        <span className="font-mono font-semibold capitalize">{vol}</span>
      </div>
      {vix && (
        <div>
          <span className="text-gray-500">India VIX </span>
          <span className="font-mono font-semibold">{Number(vix).toFixed(1)}</span>
        </div>
      )}
    </div>
  )
}

// Turns a raw bus message into a one-line, human-readable summary — so the
// feed reads as a story ("news came in → analysed → decision made") instead
// of just a stream of internal event-type names.
function summarise(type: string, payload: any): string {
  if (!payload || typeof payload !== 'object') return ''
  switch (type) {
    case 'guardian_alert':
      return `${payload.severity ?? ''} · ${(payload.reason ?? '').slice(0, 90)}`
    case 'pod_signal':
      return `${payload.direction ?? ''} · conviction ${Number(payload.conviction ?? 0).toFixed(2)} · ${(payload.rationale ?? '').slice(0, 80)}`
    case 'order_filled':
    case 'order_placed': {
      const o = payload.order ?? {}
      const r = payload.result ?? {}
      return `${o.side ?? ''} ${o.quantity ?? ''} @ ₹${r.average_fill_price ?? o.price ?? '—'}`
    }
    case 'idea_approved':
    case 'idea_rejected':
      return `${payload.reasoning_summary ?? payload.reasoning ?? ''}`.slice(0, 110)
    case 'regime_change':
      return `now ${payload.regime ?? payload.trend ?? '—'}`
    case 'data_fetched':
      return payload.detail ?? ''
    default:
      return ''
  }
}

// Which symbol an event is actually about, so the feed can be filtered down
// to one stock's whole story (news → decision → trade) instead of everything
// interleaved together.
function extractSymbol(type: string, payload: any): string {
  if (!payload || typeof payload !== 'object') return ''
  if (type === 'order_filled' || type === 'order_placed') return payload.order?.symbol ?? ''
  if (type === 'data_fetched') {
    // detail is a free-text string like "Fetched quote SBIN.NS" — every one of
    // these rows is actually a different stock, they just all show the same
    // "data_fetched" label with no symbol column, so they look identical at a
    // glance. Pull the ticker out so each row is visibly distinct.
    const m = String(payload.detail ?? '').match(/\b([A-Z][A-Z0-9&]*)\.NS\b/)
    return m ? m[1] : ''
  }
  return payload.symbol ?? ''
}

// Visual stage marker so the news → decision → trade sequence is obvious
// at a glance, not just inferred from the event-type label.
const STAGE_ICON: Record<string, string> = {
  guardian_alert:  '📰',
  pod_signal:      '🤖',
  idea_approved:   '🤖',
  idea_rejected:   '🤖',
  order_filled:    '💰',
  order_placed:    '💰',
  regime_change:   '📊',
  data_fetched:    '📈',
}

function EventFeed() {
  const [symbolFilter, setSymbolFilter] = useState('')
  const allEvents = useStore(s => s.liveEvents.filter(e =>
    e.type !== 'heartbeat' && e.type !== 'pong' && e.type !== 'connected'
  ))
  const events = (symbolFilter
    ? allEvents.filter(e => extractSymbol(e.type, e.payload).toUpperCase() === symbolFilter.toUpperCase())
    : allEvents
  ).slice(0, 30)

  return (
    <div className="card h-72 overflow-y-auto flex flex-col gap-1">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-mono text-gray-400">Live Events</span>
        <input
          className="bg-gray-900 border border-gray-700 rounded px-2 py-0.5 text-xs text-white placeholder:text-gray-600 focus:outline-none focus:border-brand-500 w-32"
          placeholder="Filter symbol…"
          value={symbolFilter}
          onChange={e => setSymbolFilter(e.target.value.toUpperCase())}
        />
      </div>
      {events.length === 0 && (
        <div className="text-gray-600 text-sm">
          {symbolFilter ? `No events yet for ${symbolFilter}` : 'System running — waiting for trading signals…'}
        </div>
      )}
      {events.map((e, i) => {
        const detail  = summarise(e.type, e.payload)
        const symbol  = extractSymbol(e.type, e.payload)
        return (
          <div key={i} className="flex gap-2 text-xs font-mono border-b border-gray-800 pb-1">
            <span className="text-gray-600 shrink-0">{fmtTsTime(e.ts)}</span>
            <span className="shrink-0">{STAGE_ICON[e.type] ?? '•'}</span>
            {symbol && <span className="text-emerald-400 shrink-0 w-20 truncate">{symbol}</span>}
            <span className="text-brand-500 shrink-0 w-28 truncate">{e.type}</span>
            <span className="text-gray-400 truncate">{detail || e.source}</span>
          </div>
        )
      })}
    </div>
  )
}

interface FlowState { title: string; lines: string[] }
interface GraphNodeLite { id: string; state: FlowState }

function NewsSourcesCard() {
  const selectedPortfolioId = useStore(s => s.selectedPortfolioId)
  const { data: graph } = useQuery<{ nodes: GraphNodeLite[] }>({
    queryKey: ['graph-news-sources', selectedPortfolioId],
    queryFn:  () => fetchJson('/system/graph'),
    refetchInterval: 10000,
  })
  const newsNode = graph?.nodes.find(n => n.id === 'news')
  if (!newsNode) return null

  return (
    <div className="card flex flex-col gap-1">
      <div className="text-xs font-mono text-gray-400 mb-1">News Sources</div>
      {newsNode.state.lines.map((line, i) => (
        <div key={i} className="text-xs font-mono text-gray-500">{line}</div>
      ))}
    </div>
  )
}

export default function Dashboard() {
  const selectedPortfolioId = useStore(s => s.selectedPortfolioId)
  const { data: snap } = useQuery<CapitalSnapshot>({
    queryKey: ['capital', selectedPortfolioId],
    queryFn:  () => fetchJson('/portfolio/snapshot'),
    refetchInterval: 5000,
  })

  const { data: status } = useQuery({
    queryKey: ['status', selectedPortfolioId],
    queryFn:  () => fetchJson('/portfolio/status'),
    refetchInterval: 3000,
  })

  const dailyPnl = snap ? parseFloat(snap.daily_pnl) : 0

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Mission Control</h1>
        <div className="flex items-center gap-6 text-sm">
          <div>
            <span className="text-gray-500">Total Capital </span>
            <span className="font-mono font-semibold">{fmtInr(snap?.total_capital)}</span>
          </div>
          <div>
            <span className="text-gray-500">Day P&L </span>
            <span className={cn('font-mono font-semibold', dailyPnl >= 0 ? 'pnl-pos' : 'pnl-neg')}>
              {dailyPnl >= 0 ? '+' : ''}{fmtInr(dailyPnl)}
            </span>
          </div>
          <BuySuggestionsButton />
        </div>
      </div>

      {/* Latest Trace — always shows the most recent finished story, no need to catch it live */}
      <LatestTraceCard />

      {/* Regime */}
      <RegimeCard status={status} />

      {/* Capital Pillars */}
      <div className="grid grid-cols-3 gap-4">
        {snap && Object.entries(snap.pillar_allocations).map(([name, data]) => (
          <PillarCard key={name} name={name.replace(/_/g, ' ')} data={data} />
        ))}
      </div>

      {/* News sources + Live Events */}
      <div className="grid grid-cols-3 gap-4">
        <div className="col-span-1">
          <NewsSourcesCard />
        </div>
        <div className="col-span-2">
          <EventFeed />
        </div>
      </div>
    </div>
  )
}
