import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchJson, type Position } from '../lib/api'
import { fmtInr, fmtPrice, fmt, cn } from '../lib/utils'
import { TableSkeleton } from '../components/Skeleton'

import { useStore } from '../hooks/useStore'
import { DebateModal } from '../components/DebateModal'

interface PMHolding {
  order_id:    string
  symbol:      string
  exchange:    string
  quantity:    number
  entry_price: number
  entry_ts:    string
  stop_loss:   number | null
  take_profit: number | null
  rationale:   string
  source:      string
  strategy:    string
}

interface PMDecision {
  symbol:        string
  action:        string
  sell_pct:      number
  reasoning:     string
  trigger:       string
  headline?:     string | null
  pnl_pct:       number
  current_price: number | null
  ts:            string
  entry_price?:  number
  stop_loss?:    number | null
  take_profit?:  number | null
}

// ── helpers ───────────────────────────────────────────────────────────────────

function fmtTs(iso: string | null | undefined): string {
  if (!iso) return '—'
  const utc = iso.endsWith('Z') ? iso : iso + 'Z'
  return new Date(utc).toLocaleString('en-IN', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

function hoursLeft(iso: string | null | undefined): number | null {
  if (!iso) return null
  return (new Date(iso).getTime() - Date.now()) / 3_600_000
}

// ── SL/TP progress bar ────────────────────────────────────────────────────────

function SLTPBar({ sl, tp, current }: { sl: number | null | undefined; tp: number | null | undefined; current: number | null | undefined }) {
  if (sl == null || tp == null || current == null || tp <= sl) return null
  const pct      = Math.max(0, Math.min(100, ((current - sl) / (tp - sl)) * 100))
  const barColor = pct < 20 ? 'bg-red-500' : pct > 80 ? 'bg-green-500' : 'bg-blue-500'
  const label    = pct < 20 ? 'Near stop-loss!' : pct > 80 ? 'Near target!' : `${pct.toFixed(0)}% to target`
  const labelCol = pct < 20 ? 'text-red-400' : pct > 80 ? 'text-green-400' : 'text-gray-500'
  return (
    <div className="space-y-1">
      <div className="flex justify-between items-center text-xs">
        <span className="text-red-400 font-mono">SL {fmtInr(sl)}</span>
        <span className={cn('text-[10px]', labelCol)}>{label}</span>
        <span className="text-green-400 font-mono">TP {fmtInr(tp)}</span>
      </div>
      <div className="relative h-2 bg-gray-800 rounded-full overflow-hidden">
        <div className={cn('h-full rounded-full transition-all duration-500', barColor)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

// ── source pill cluster ───────────────────────────────────────────────────────

function SourcePills({ pod, desk, strategy }: { pod: string | null; desk: string | null; strategy: string | null }) {
  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {desk === 'long_term_desk' && (
        <span className="text-[10px] font-medium bg-purple-900/50 text-purple-300 border border-purple-800/50 px-2 py-0.5 rounded-full">
          Long-term Desk
        </span>
      )}
      {pod && pod !== 'portfolio_manager' && (
        <span className="text-[10px] font-medium bg-gray-800 text-gray-400 border border-gray-700 px-2 py-0.5 rounded-full">
          {pod.replace(/_/g, ' ')}
        </span>
      )}
      {strategy && (
        <span className="text-[10px] font-medium bg-blue-950/60 text-blue-400 border border-blue-900/50 px-2 py-0.5 rounded-full">
          {strategy.replace(/_/g, ' ')}
        </span>
      )}
    </div>
  )
}

// ── single position card ──────────────────────────────────────────────────────

function PositionCard({ pos, rationale, onViewDebate }: { pos: Position; rationale: string; onViewDebate: (s: string) => void }) {
  const pnl    = parseFloat(pos.unrealized_pnl || '0')
  const pnlPct = pos.unrealized_pnl_pct ?? 0
  const sl     = pos.stop_loss    ? parseFloat(pos.stop_loss)    : null
  const tp     = pos.take_profit  ? parseFloat(pos.take_profit)  : null
  const cur    = pos.current_price ? parseFloat(pos.current_price) : null
  const avgP   = parseFloat(pos.average_price)
  const hl     = hoursLeft(pos.max_hold_until)
  const expiringSoon = hl !== null && hl < 2 && hl >= 0
  const expired      = hl !== null && hl < 0

  return (
    <div className={cn(
      'rounded-xl border p-4 flex flex-col gap-3 bg-gray-900',
      pnl > 0     ? 'border-green-900/60'  :
      pnl < -300  ? 'border-red-900/60'    :
                    'border-gray-800',
    )}>

      {/* header ── symbol + P&L */}
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1.5 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono font-bold text-lg text-white">{pos.symbol}</span>
            <span className="text-xs bg-gray-800 text-gray-400 px-1.5 py-0.5 rounded">{pos.exchange}</span>
            <span className={cn(
              'text-xs font-semibold px-1.5 py-0.5 rounded',
              pos.side.toUpperCase() === 'BUY' ? 'bg-green-900/50 text-green-300' : 'bg-red-900/50 text-red-300',
            )}>
              {pos.side.toUpperCase()}
            </span>
          </div>
          <SourcePills pod={pos.source_pod} desk={pos.source_desk} strategy={pos.strategy} />
        </div>

        <div className="text-right shrink-0">
          <div className={cn('text-xl font-mono font-bold', pnl >= 0 ? 'pnl-pos' : 'pnl-neg')}>
            {pnl >= 0 ? '+' : ''}{fmtInr(pnl)}
          </div>
          <div className={cn('text-sm font-mono', pnlPct >= 0 ? 'pnl-pos' : 'pnl-neg')}>
            {pnlPct >= 0 ? '+' : ''}{fmt(pnlPct, 2)}%
          </div>
        </div>
      </div>

      {/* price row */}
      <div className="flex items-center gap-2 text-sm bg-gray-800/50 rounded-lg px-3 py-2">
        <span className="text-gray-500">×{pos.quantity} shares</span>
        <span className="text-gray-600 mx-1">·</span>
        <span className="font-mono text-gray-300">entry {fmtPrice(avgP)}</span>
        <span className="text-gray-600 mx-1">→</span>
        <span className={cn('font-mono font-semibold', cur == null ? 'text-gray-600' : pnl >= 0 ? 'text-green-300' : 'text-red-300')}>
          {cur != null ? `now ${fmtPrice(cur)}` : 'price unavailable'}
        </span>
      </div>

      {/* SL/TP bar */}
      {(sl || tp) && <SLTPBar sl={sl} tp={tp} current={cur} />}

      {/* rationale */}
      <div className="rounded-lg bg-blue-950/20 border border-blue-900/30 px-3 py-2.5">
        <div className="text-[10px] uppercase tracking-widest text-blue-500 font-semibold mb-1">
          Why this position was opened
        </div>
        {rationale ? (
          <div className="text-xs text-gray-300 leading-relaxed">{rationale}</div>
        ) : (
          <div className="text-xs text-gray-500 leading-relaxed">
            Full committee reasoning is loading — restart the backend to hydrate rationale from the
            decision ledger. Position was opened by{' '}
            <span className="text-gray-400 font-medium">
              {pos.source_desk === 'long_term_desk' ? 'the Long-term Desk (7-agent AI committee)' :
               pos.source_pod ? `${pos.source_pod.replace(/_/g, ' ')} pod` : 'automated system'}
            </span>
            {pos.strategy ? ` using ${pos.strategy.replace(/_/g, ' ')} strategy` : ''}.
          </div>
        )}
      </div>

      {/* footer */}
      <div className="flex items-center justify-between text-[10px] text-gray-600 pt-1 border-t border-gray-800/60 flex-wrap gap-2">
        <span>Opened {fmtTs(pos.opened_at)}</span>
        {pos.max_hold_until && (
          <span className={cn(
            'font-medium',
            expired      ? 'text-red-400'    :
            expiringSoon ? 'text-orange-400' : 'text-gray-600',
          )}>
            {expired
              ? '⚠ Max hold time exceeded'
              : expiringSoon
                ? `⏱ Expires in ${Math.round(hl! * 60)}m`
                : `Hold until ${fmtTs(pos.max_hold_until)}`}
          </span>
        )}
        <button
          onClick={() => onViewDebate(pos.symbol)}
          className="text-[10px] text-purple-500 hover:text-purple-300 border border-purple-900/50 rounded px-2 py-0.5 hover:border-purple-700 transition-colors ml-auto"
        >
          View full debate →
        </button>
      </div>

    </div>
  )
}

// ── decision card ─────────────────────────────────────────────────────────────

function DecisionCard({ d }: { d: PMDecision }) {
  const isMonitor = d.action === 'MONITORING'
  const isExit    = d.action === 'SELL_ALL'
  const isPartial = d.action === 'SELL_PARTIAL'
  const isHold    = d.action === 'HOLD'
  const pnlColor  = d.pnl_pct >= 0 ? 'text-green-400' : 'text-red-400'
  const sl        = d.stop_loss   ?? null
  const tp        = d.take_profit ?? null
  const cur       = d.current_price

  // Monitoring checks: compact single-line row, grey
  if (isMonitor) {
    return (
      <div className="flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-900 border border-gray-800 text-xs">
        <span className="font-mono font-semibold text-gray-400">{d.symbol}</span>
        <span className="text-gray-700 text-[10px] uppercase tracking-wider border border-gray-700 px-1.5 py-0.5 rounded-full">
          monitoring
        </span>
        <span className={cn('font-mono', pnlColor)}>{d.pnl_pct >= 0 ? '+' : ''}{d.pnl_pct?.toFixed(2)}%</span>
        <span className="text-gray-600 flex-1 truncate">{d.reasoning}</span>
        {d.ts && <span className="text-gray-700 shrink-0">{fmtTs(d.ts)}</span>}
      </div>
    )
  }

  return (
    <div className={cn(
      'rounded-xl border px-4 py-3 space-y-2',
      isExit    ? 'bg-red-950/30 border-red-900/50'        :
      isPartial ? 'bg-yellow-950/20 border-yellow-900/50'  :
      isHold    ? 'bg-green-950/20 border-green-900/40'    :
                  'bg-gray-900 border-gray-800',
    )}>
      {/* top row */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-mono font-bold text-base text-white">{d.symbol}</span>

        <span className={cn(
          'text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full border',
          isExit    ? 'bg-red-900/60 text-red-300 border-red-800'          :
          isPartial ? 'bg-yellow-900/60 text-yellow-300 border-yellow-800' :
          isHold    ? 'bg-green-900/60 text-green-300 border-green-800'    :
                      'bg-gray-800 text-gray-400 border-gray-700',
        )}>
          {d.action}{isPartial ? ` · ${d.sell_pct}%` : ''}
        </span>

        <span className={cn('text-sm font-mono font-semibold', pnlColor)}>
          {d.pnl_pct >= 0 ? '+' : ''}{d.pnl_pct?.toFixed(2)}%
        </span>

        <span className="text-[10px] text-gray-500 ml-auto">
          trigger: <span className="text-gray-400">{d.trigger}</span>
        </span>

        {d.ts && (
          <span className="text-[10px] text-gray-600">{fmtTs(d.ts)}</span>
        )}
      </div>

      {/* reasoning */}
      <div className="text-sm text-gray-200 leading-relaxed">{d.reasoning}</div>

      {/* news headline if available */}
      {d.headline && (
        <div className="text-xs text-gray-500 bg-gray-800/50 rounded px-2 py-1.5">
          <span className="text-gray-600">News: </span>{d.headline}
        </div>
      )}

      {/* SL/TP context */}
      {(sl || tp) && cur && (
        <div className="pt-1">
          <SLTPBar sl={sl} tp={tp} current={cur} />
        </div>
      )}

      {/* entry vs current */}
      {d.entry_price && cur && (
        <div className="text-[10px] text-gray-600 flex gap-3">
          <span>Entry ₹{d.entry_price.toFixed(2)}</span>
          <span>→</span>
          <span>{isExit ? 'Exit' : 'Now'} ₹{cur.toFixed(2)}</span>
        </div>
      )}
    </div>
  )
}

// ── page ──────────────────────────────────────────────────────────────────────

export default function PortfolioManagerPage() {
  const [debateSymbol, setDebateSymbol] = useState<string | null>(null)

  const { data: positions = [], isLoading } = useQuery<Position[]>({
    queryKey:        ['positions'],
    queryFn:         () => fetchJson('/portfolio/positions'),
    refetchInterval: 10_000,
  })

  const { data: holdings = [] } = useQuery<PMHolding[]>({
    queryKey:        ['pm-holdings'],
    queryFn:         () => fetchJson('/portfolio/holdings'),
    refetchInterval: 10_000,
  })

  const { data: apiDecisions = [] } = useQuery<PMDecision[]>({
    queryKey:        ['pm-decisions'],
    queryFn:         () => fetchJson('/portfolio/decisions'),
    refetchInterval: 15_000,
  })

  // also catch decisions that just arrived over WebSocket this session
  const liveEvents = useStore(s => s.liveEvents)
  const wsDecisions: PMDecision[] = liveEvents
    .filter(e => e.type === 'portfolio_decision')
    .map(e => e.payload as PMDecision)

  // merge: WS decisions first (most recent), then API log, dedupe by ts+symbol
  const seen  = new Set<string>()
  const decisions: PMDecision[] = []
  for (const d of [...wsDecisions, ...apiDecisions]) {
    const key = `${d.symbol}_${d.ts}_${d.action}`
    if (!seen.has(key)) { seen.add(key); decisions.push(d) }
  }

  // rationale lookup: keyed by symbol (PM holdings)
  const rationaleMap = Object.fromEntries(holdings.map(h => [h.symbol, h.rationale]))

  // summary stats
  const totalPnl      = positions.reduce((s, p) => s + parseFloat(p.unrealized_pnl || '0'), 0)
  const deployedCap   = positions.reduce((s, p) => s + p.quantity * parseFloat(p.average_price || '0'), 0)
  const winners       = positions.filter(p => parseFloat(p.unrealized_pnl || '0') > 0).length
  const losers        = positions.filter(p => parseFloat(p.unrealized_pnl || '0') < 0).length

  return (
    <div className="space-y-8">

      {/* ── Page header + stats ──────────────────────────────── */}
      <div className="space-y-4">
        <div>
          <h1 className="text-xl font-semibold text-white">Portfolio Manager</h1>
          <p className="text-sm text-gray-500 mt-1">
            Tracks every open position with full context. Receives all incoming news and Reddit signals
            for held stocks, evaluates them via LLM, and decides to hold, trim, or exit — logging
            every decision with reasoning.
          </p>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="card py-3 text-center">
            <div className="text-2xl font-bold text-white">{positions.length}</div>
            <div className="text-xs text-gray-500 mt-0.5">Open Positions</div>
          </div>
          <div className="card py-3 text-center">
            <div className={cn('text-2xl font-bold font-mono', totalPnl >= 0 ? 'pnl-pos' : 'pnl-neg')}>
              {totalPnl >= 0 ? '+' : ''}{fmtInr(totalPnl)}
            </div>
            <div className="text-xs text-gray-500 mt-0.5">Unrealised P&L</div>
          </div>
          <div className="card py-3 text-center">
            <div className="text-2xl font-bold font-mono text-white">
              {fmtInr(deployedCap)}
            </div>
            <div className="text-xs text-gray-500 mt-0.5">Capital Deployed</div>
          </div>
          <div className="card py-3 text-center space-y-1.5">
            {positions.length === 0 ? (
              <div className="text-2xl font-bold text-gray-600">—</div>
            ) : (
              <>
                <div className="text-2xl font-bold font-mono text-white">
                  {positions.length > 0
                    ? `${Math.round((winners / positions.length) * 100)}%`
                    : '—'}
                </div>
                <div className="w-full h-1.5 bg-gray-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-green-500 rounded-full"
                    style={{ width: `${Math.round((winners / positions.length) * 100)}%` }}
                  />
                </div>
                <div className="flex justify-between text-[10px] text-gray-500">
                  <span className="text-green-400">{winners} up</span>
                  <span className="text-red-400">{losers} down</span>
                </div>
              </>
            )}
            <div className="text-xs text-gray-500">Positions In Profit</div>
          </div>
        </div>
      </div>

      {/* ── How the PM works (inline explainer) ─────────────── */}
      <div className="rounded-xl border border-gray-800 bg-gray-900/60 px-5 py-4">
        <div className="text-xs font-semibold uppercase tracking-widest text-gray-500 mb-3">How decisions are made</div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs text-gray-400">
          <div className="space-y-1">
            <div className="text-white font-semibold">1 · News / Reddit</div>
            <div>Every article or Reddit post that mentions a held stock is routed here. The PM sends the full position context + the news to the LLM, which returns HOLD / SELL_ALL / SELL_PARTIAL with a written reason.</div>
          </div>
          <div className="space-y-1">
            <div className="text-white font-semibold">2 · Stop-loss & Target</div>
            <div>Every 60 seconds the PM checks live prices for all holdings. If price drops to the stop-loss or rises to the take-profit it sells the full position immediately — no LLM needed.</div>
          </div>
          <div className="space-y-1">
            <div className="text-white font-semibold">3 · Decision log</div>
            <div>Every decision (HOLD or exit) is logged with the symbol, action, P&L at time, trigger, and the LLM's reasoning. The last 200 decisions are kept in memory and shown below.</div>
          </div>
        </div>
      </div>

      {/* ── Position cards ───────────────────────────────────── */}
      <div className="space-y-4">
        <h2 className="text-base font-semibold text-white">
          Active Positions
          <span className="ml-2 text-xs font-normal text-gray-500">auto-refreshes every 10s</span>
        </h2>

        {isLoading && <TableSkeleton rows={4} cols={7} />}
        {!isLoading && positions.length === 0 && (
          <div className="card text-center text-gray-600 py-12">No open positions</div>
        )}

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {positions.map(p => (
            <PositionCard
              key={p.id || p.symbol}
              pos={p}
              rationale={rationaleMap[p.symbol] ?? ''}
              onViewDebate={setDebateSymbol}
            />
          ))}
        </div>
      </div>

      {/* ── Decision log ─────────────────────────────────────── */}
      <div className="space-y-4">
        <div className="flex items-center gap-3 flex-wrap">
          <h2 className="text-base font-semibold text-white">PM Decision Log</h2>
          {decisions.length > 0 && (
            <span className="text-xs text-gray-500 bg-gray-800 rounded-full px-2 py-0.5">
              {decisions.length} entries
            </span>
          )}
          <span className="text-xs text-gray-600">
            Grey rows = 60-second price check (no action needed). Coloured cards = HOLD / SELL decision triggered by news or price target.
          </span>
        </div>

        {decisions.length === 0 ? (
          <div className="card text-center text-gray-600 py-8 text-sm">
            No decisions yet this session. The PM checks prices every 60s and logs every evaluation here — including when it decides to do nothing.
          </div>
        ) : (
          <div className="space-y-2">
            {decisions.slice(0, 50).map((d, i) => <DecisionCard key={i} d={d} />)}
            {decisions.length > 50 && (
              <div className="text-xs text-gray-600 text-center py-2">
                Showing 50 of {decisions.length} entries
              </div>
            )}
          </div>
        )}
      </div>

      {debateSymbol && (
        <DebateModal symbol={debateSymbol} onClose={() => setDebateSymbol(null)} />
      )}

    </div>
  )
}
