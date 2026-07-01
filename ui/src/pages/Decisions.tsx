import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchJson, type Decision, type Trade } from '../lib/api'
import { fmtPrice, cn } from '../lib/utils'
import { DebateModal } from '../components/DebateModal'

// ── helpers ───────────────────────────────────────────────────────────────────

function fmtTs(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-IN', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

function parseJson(raw: string | null | undefined): Record<string, unknown> {
  if (!raw) return {}
  try { return JSON.parse(raw) } catch { return {} }
}

// ── outcome badge ─────────────────────────────────────────────────────────────

function OutcomeBadge({ outcome }: { outcome: string | null }) {
  if (!outcome) return null
  const upper = outcome.toUpperCase()
  const isBought   = upper.includes('BOUGHT') || upper.includes('EXECUTED') && !upper.includes('NOT')
  const isRejected = upper.includes('REJECTED') || upper.includes('NOT EXECUTED') || upper.includes('BLOCKED')
  const isWaiting  = upper.includes('WAITING') || upper.includes('BETTER ENTRY') || upper.includes('DEFERRED')
  const isSold     = upper.includes('SOLD') || upper.includes('SELL')

  return (
    <span className={cn(
      'text-xs font-semibold px-2 py-0.5 rounded-full border',
      isBought   ? 'bg-green-900/60 text-green-300 border-green-800'    :
      isSold     ? 'bg-blue-900/60 text-blue-300 border-blue-800'       :
      isRejected ? 'bg-red-900/60 text-red-300 border-red-800'          :
      isWaiting  ? 'bg-yellow-900/60 text-yellow-300 border-yellow-800' :
                   'bg-gray-800 text-gray-400 border-gray-700',
    )}>
      {isBought   ? 'BOUGHT'         :
       isSold     ? 'SOLD'           :
       isRejected ? 'REJECTED'       :
       isWaiting  ? 'WAITING'        :
                    outcome.slice(0, 30)}
    </span>
  )
}

// ── agent label ───────────────────────────────────────────────────────────────

function agentLabel(agentId: string): { label: string; color: string } {
  if (agentId.includes('committee_chair'))   return { label: 'Committee Chair',   color: 'text-purple-400' }
  if (agentId.includes('bull'))              return { label: 'Bull Advocate',      color: 'text-green-400'  }
  if (agentId.includes('bear'))              return { label: 'Bear Advocate',      color: 'text-red-400'    }
  if (agentId.includes('devil'))             return { label: "Devil's Advocate",   color: 'text-orange-400' }
  if (agentId.includes('sector'))            return { label: 'Sector Specialist',  color: 'text-blue-400'   }
  if (agentId.includes('momentum'))          return { label: 'Momentum Analyst',   color: 'text-cyan-400'   }
  if (agentId.includes('portfolio_manager')) return { label: 'Portfolio Manager',  color: 'text-yellow-400' }
  if (agentId.includes('news_watchdog'))     return { label: 'News Watchdog',      color: 'text-pink-400'   }
  if (agentId.includes('risk'))              return { label: 'Risk Gate',          color: 'text-red-400'    }
  if (agentId.includes('execution'))        return { label: 'Execution Trader',   color: 'text-teal-400'   }
  if (agentId.includes('alloc'))            return { label: 'Allocation Chair',   color: 'text-indigo-400' }
  return { label: agentId.replace(/[._]/g, ' '), color: 'text-gray-400' }
}

// ── decision card ─────────────────────────────────────────────────────────────

function DecisionCard({ d, expanded, onToggle, onViewDebate }: {
  d: Decision
  expanded: boolean
  onToggle: () => void
  onViewDebate: (symbol: string) => void
}) {
  const outputs = parseJson(d.outputs)
  const inputs  = parseJson(d.inputs)
  const agent   = agentLabel(d.agent_id)
  const isChair = d.agent_id.includes('committee_chair')

  const finalConviction = outputs.final_conviction as number | undefined
  const approved        = outputs.approved as boolean | undefined
  const bullConv        = inputs.bull_conviction as number | undefined
  const bearConv        = inputs.bear_conviction as number | undefined

  return (
    <div className={cn(
      'rounded-xl border overflow-hidden',
      isChair ? 'border-purple-900/60 bg-purple-950/10' : 'border-gray-800 bg-gray-900',
    )}>
      {/* header — always visible */}
      <button
        className="w-full text-left px-4 py-3 flex items-start gap-3"
        onClick={onToggle}
      >
        <div className="flex-1 space-y-1.5">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-gray-600 font-mono">{fmtTs(d.event_ts)}</span>
            <span className={cn('text-xs font-semibold', agent.color)}>{agent.label}</span>
            {d.symbol && (
              <span className="font-mono font-bold text-white text-sm">{d.symbol}</span>
            )}
            {isChair && typeof approved === 'boolean' && (
              <span className={cn(
                'text-[10px] font-bold uppercase px-1.5 py-0.5 rounded-full border',
                approved
                  ? 'bg-green-900/60 text-green-300 border-green-800'
                  : 'bg-red-900/60 text-red-300 border-red-800',
              )}>
                {approved ? 'Approved' : 'Rejected'}
              </span>
            )}
            {finalConviction != null && (
              <span className="text-xs text-gray-500">
                conviction {(finalConviction * 100).toFixed(0)}%
              </span>
            )}
            {bullConv != null && bearConv != null && (
              <span className="text-xs text-gray-600">
                Bull {(bullConv * 100).toFixed(0)}% · Bear {(bearConv * 100).toFixed(0)}%
              </span>
            )}
            <OutcomeBadge outcome={d.outcome} />
          </div>

          {/* one-line preview when collapsed */}
          {!expanded && d.reasoning && (
            <p className="text-xs text-gray-500 line-clamp-2">{d.reasoning}</p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {d.symbol && !expanded && (
            <button
              onClick={e => { e.stopPropagation(); onViewDebate(d.symbol!) }}
              className="text-[10px] text-purple-500 hover:text-purple-300 border border-purple-900/50 rounded px-1.5 py-0.5 hover:border-purple-700 transition-colors"
            >
              Full debate
            </button>
          )}
          <span className="text-gray-600 text-xs">{expanded ? '▲' : '▼'}</span>
        </div>
      </button>

      {/* expanded body */}
      {expanded && (
        <div className="px-4 pb-4 space-y-3 border-t border-gray-800">
          {/* full reasoning */}
          {d.reasoning && (
            <div className="space-y-1 pt-3">
              <div className="text-[10px] uppercase tracking-widest text-gray-500 font-semibold">Reasoning</div>
              <p className="text-sm text-gray-300 leading-relaxed whitespace-pre-line">{d.reasoning}</p>
            </div>
          )}

          {/* outcome */}
          {d.outcome && (
            <div className="rounded-lg px-3 py-2 bg-gray-800/60 space-y-0.5">
              <div className="text-[10px] uppercase tracking-widest text-gray-500 font-semibold">Outcome / What happened</div>
              <p className="text-sm text-gray-200 leading-relaxed">{d.outcome}</p>
            </div>
          )}

          {/* vote breakdown for committee chair */}
          {isChair && (bullConv != null || bearConv != null) && (
            <div className="grid grid-cols-3 gap-2 text-xs">
              {bullConv != null && (
                <div className="rounded bg-green-950/40 border border-green-900/40 px-2 py-1.5 text-center">
                  <div className="text-green-400 font-bold">{(bullConv * 100).toFixed(0)}%</div>
                  <div className="text-gray-500">Bull conviction</div>
                </div>
              )}
              {bearConv != null && (
                <div className="rounded bg-red-950/40 border border-red-900/40 px-2 py-1.5 text-center">
                  <div className="text-red-400 font-bold">{(bearConv * 100).toFixed(0)}%</div>
                  <div className="text-gray-500">Bear conviction</div>
                </div>
              )}
              {finalConviction != null && (
                <div className="rounded bg-purple-950/40 border border-purple-900/40 px-2 py-1.5 text-center">
                  <div className="text-purple-400 font-bold">{(finalConviction * 100).toFixed(0)}%</div>
                  <div className="text-gray-500">Final conviction</div>
                </div>
              )}
            </div>
          )}

          {/* debate button — only for stocks with a symbol */}
          {d.symbol && (
            <div className="pt-1">
              <button
                onClick={e => { e.stopPropagation(); onViewDebate(d.symbol!) }}
                className="btn-ghost text-xs py-1 px-3 border-purple-900/60 text-purple-400 hover:text-purple-200 hover:border-purple-700"
              >
                View full debate for {d.symbol} →
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── trade row ─────────────────────────────────────────────────────────────────

function TradeRow({ t, onViewDebate }: { t: Trade; onViewDebate: (s: string) => void }) {
  const isBuy = t.side === 'buy'
  const value = t.price * t.quantity
  return (
    <div className={cn(
      'rounded-xl border px-4 py-3 flex items-center gap-3 flex-wrap',
      isBuy ? 'border-green-900/50 bg-green-950/10' : 'border-red-900/40 bg-red-950/10',
    )}>
      <span className="text-xs text-gray-600 font-mono">{fmtTs(t.timestamp)}</span>

      <span className={cn(
        'text-[10px] font-bold uppercase px-2 py-0.5 rounded-full border',
        isBuy ? 'bg-green-900/60 text-green-300 border-green-800' : 'bg-red-900/60 text-red-300 border-red-800',
      )}>
        {isBuy ? 'BOUGHT' : 'SOLD'}
      </span>

      <span className="font-mono font-bold text-white">{t.symbol}</span>
      <span className="text-sm text-gray-400">×{t.quantity} @ {fmtPrice(t.price)}</span>
      <span className="text-sm font-mono text-gray-300">{fmtPrice(value)}</span>

      {t.pnl !== 0 && (
        <span className={cn('text-sm font-mono font-semibold', t.pnl > 0 ? 'pnl-pos' : 'pnl-neg')}>
          {t.pnl > 0 ? '+' : ''}{fmtPrice(t.pnl)}
        </span>
      )}

      <button
        onClick={() => onViewDebate(t.symbol)}
        className="text-[10px] text-purple-500 hover:text-purple-300 border border-purple-900/50 rounded px-1.5 py-0.5 hover:border-purple-700 transition-colors ml-auto"
      >
        Full debate
      </button>

      <span className="text-xs text-gray-600">
        {t.source_pod?.replace(/_/g, ' ') || t.source_desk?.replace(/_/g, ' ') || '—'}
        {t.strategy ? ` · ${t.strategy.replace(/_/g, ' ')}` : ''}
      </span>
    </div>
  )
}

// ── page ──────────────────────────────────────────────────────────────────────

export default function DecisionsPage() {
  const [symbol,       setSymbol]       = useState('')
  const [tab,          setTab]          = useState<'all' | 'debate' | 'trades'>('all')
  const [expanded,     setExpanded]     = useState<Set<number>>(new Set())
  const [debateSymbol, setDebateSymbol] = useState<string | null>(null)

  const params = new URLSearchParams()
  if (symbol) params.set('symbol', symbol)
  params.set('limit', '200')

  const { data: decisions = [], isLoading: dLoading } = useQuery<Decision[]>({
    queryKey:        ['decisions', symbol],
    queryFn:         () => fetchJson(`/decisions/?${params}`),
    refetchInterval: 30_000,
  })

  const { data: trades = [], isLoading: tLoading } = useQuery<Trade[]>({
    queryKey:        ['trades'],
    queryFn:         () => fetchJson('/portfolio/trades'),
    refetchInterval: 30_000,
  })

  const toggleExpand = (i: number) => {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(i) ? next.delete(i) : next.add(i)
      return next
    })
  }

  const expandAll  = () => setExpanded(new Set(decisions.map((_, i) => i)))
  const collapseAll = () => setExpanded(new Set())

  const filteredTrades = symbol
    ? trades.filter(t => t.symbol === symbol)
    : trades

  const chairDecisions = decisions.filter(d => d.agent_id.includes('committee_chair'))
  const allDecisions   = decisions

  return (
    <div className="space-y-6">

      {/* ── header ───────────────────────────────────────────── */}
      <div>
        <h1 className="text-xl font-semibold">Activity Journal</h1>
        <p className="text-sm text-gray-500 mt-1">
          Complete record of every buy/sell decision — why it was made, who debated it, what happened.
        </p>
      </div>

      {/* ── controls ─────────────────────────────────────────── */}
      <div className="flex items-center gap-3 flex-wrap">
        <input
          className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white placeholder:text-gray-600 focus:outline-none focus:border-brand-500 w-44"
          placeholder="Filter by symbol…"
          value={symbol}
          onChange={e => setSymbol(e.target.value.toUpperCase())}
        />

        <div className="flex rounded-lg overflow-hidden border border-gray-700 text-sm">
          {(['all', 'debate', 'trades'] as const).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={cn(
                'px-3 py-1.5 font-medium',
                tab === t ? 'bg-brand-500 text-white' : 'bg-gray-900 text-gray-400 hover:text-white',
              )}
            >
              {t === 'all' ? 'All Decisions' : t === 'debate' ? 'Debate Summaries' : 'Executed Trades'}
            </button>
          ))}
        </div>

        {tab !== 'trades' && (
          <div className="flex gap-2 ml-auto">
            <button onClick={expandAll}   className="text-xs text-gray-500 hover:text-white">Expand all</button>
            <button onClick={collapseAll} className="text-xs text-gray-500 hover:text-white">Collapse all</button>
          </div>
        )}
      </div>

      {/* ── trades tab ───────────────────────────────────────── */}
      {tab === 'trades' && (
        <div className="space-y-2">
          <div className="text-sm text-gray-500 mb-2">{filteredTrades.length} trades</div>
          {(dLoading || tLoading) && <div className="text-gray-500 text-sm">Loading…</div>}
          {filteredTrades.map((t, i) => <TradeRow key={i} t={t} onViewDebate={setDebateSymbol} />)}
          {!tLoading && filteredTrades.length === 0 && (
            <div className="card text-center text-gray-600 py-8">No trades yet</div>
          )}
        </div>
      )}

      {/* ── debate tab — committee chair summaries only ───────── */}
      {tab === 'debate' && (
        <div className="space-y-2">
          <div className="text-sm text-gray-500 mb-2">
            {chairDecisions.length} stock debates — these show the full Bull vs Bear reasoning and final committee verdict
          </div>
          {dLoading && <div className="text-gray-500 text-sm">Loading…</div>}
          {chairDecisions.map((d, i) => (
            <DecisionCard
              key={i}
              d={d}
              expanded={expanded.has(i)}
              onToggle={() => toggleExpand(i)}
              onViewDebate={setDebateSymbol}
            />
          ))}
          {!dLoading && chairDecisions.length === 0 && (
            <div className="card text-center text-gray-600 py-8">No debates recorded yet</div>
          )}
        </div>
      )}

      {/* ── all tab ──────────────────────────────────────────── */}
      {tab === 'all' && (
        <div className="space-y-2">
          <div className="text-sm text-gray-500 mb-2">{allDecisions.length} decisions recorded</div>
          {dLoading && <div className="text-gray-500 text-sm">Loading…</div>}
          {allDecisions.map((d, i) => (
            <DecisionCard
              key={i}
              d={d}
              expanded={expanded.has(i)}
              onToggle={() => toggleExpand(i)}
              onViewDebate={setDebateSymbol}
            />
          ))}
          {!dLoading && allDecisions.length === 0 && (
            <div className="card text-center text-gray-600 py-8">No decisions recorded yet</div>
          )}
        </div>
      )}

      {/* ── full debate modal ─────────────────────────────────── */}
      {debateSymbol && (
        <DebateModal symbol={debateSymbol} onClose={() => setDebateSymbol(null)} />
      )}

    </div>
  )
}
