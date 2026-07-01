import { useEffect, useRef, useState } from 'react'
import { fetchJson, postJson } from '../lib/api'
import { useStore } from '../hooks/useStore'
import { cn } from '../lib/utils'

interface Approval {
  id:        string
  symbol:    string
  source:    string
  headline:  string
  rationale: string
  link?:     string
  ts:        string
}

interface Detail extends Approval {
  current_price:        number | null
  stop_loss_price:       number | null
  take_profit_price:     number | null
  sector:                string | null
  industry:              string | null
  market_cap:            number | null
  fifty_two_week_low:    number | null
  fifty_two_week_high:   number | null
  estimated_quantity:    number
  estimated_cost:        number
}

function fmtCr(n: number | null | undefined): string {
  if (n == null) return '—'
  return n >= 1e7 ? `₹${(n / 1e7).toFixed(1)} Cr` : `₹${n.toLocaleString('en-IN')}`
}

function DetailPanel({ d }: { d: Detail }) {
  return (
    <div className="mt-2 pt-2 border-t border-gray-800 text-xs text-gray-300 space-y-1">
      <div className="grid grid-cols-2 gap-1">
        <div><span className="text-gray-500">Current price </span>{d.current_price != null ? `₹${d.current_price.toFixed(2)}` : '—'}</div>
        <div><span className="text-gray-500">Est. shares (2% cap) </span>{d.estimated_quantity || '—'}</div>
        <div><span className="text-gray-500">Stop-loss (-5%) </span><span className="text-red-400">{d.stop_loss_price != null ? `₹${d.stop_loss_price.toFixed(2)}` : '—'}</span></div>
        <div><span className="text-gray-500">Target (+10%) </span><span className="text-green-400">{d.take_profit_price != null ? `₹${d.take_profit_price.toFixed(2)}` : '—'}</span></div>
        <div><span className="text-gray-500">52w range </span>{d.fifty_two_week_low != null && d.fifty_two_week_high != null ? `₹${d.fifty_two_week_low} – ₹${d.fifty_two_week_high}` : '—'}</div>
        <div><span className="text-gray-500">Market cap </span>{fmtCr(d.market_cap)}</div>
        <div className="col-span-2"><span className="text-gray-500">Sector </span>{d.sector ?? '—'}{d.industry ? ` · ${d.industry}` : ''}</div>
      </div>
      <div className="pt-1">
        <span className="text-gray-500">Source: </span>
        {d.link
          ? <a href={d.link} target="_blank" rel="noreferrer" className="text-brand-500 hover:underline">{d.source} ↗</a>
          : <span>{d.source}</span>}
      </div>
      <div className="text-[10px] text-gray-600">
        "Target" / "stop-loss" here are the fixed risk levels a trade would use, not a price forecast — there's no model predicting where this stock actually goes.
      </div>
    </div>
  )
}

export default function BuySuggestionsButton() {
  const [pending, setPending] = useState<Approval[]>([])
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [results, setResults] = useState<Record<string, string>>({})
  const [open, setOpen] = useState(false)
  const [detailsOpen, setDetailsOpen] = useState<Record<string, boolean>>({})
  const [details, setDetails] = useState<Record<string, Detail>>({})
  const [detailsLoading, setDetailsLoading] = useState<Record<string, boolean>>({})
  const liveEvents = useStore(s => s.liveEvents)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    fetchJson<Approval[]>('/news/pending')
      .then(setPending)
      .catch(() => {})
  }, [])

  useEffect(() => {
    const fresh = liveEvents.find(e => e.type === 'news_buy_suggested')
    if (!fresh) return
    const approval = fresh.payload as Approval
    setPending(prev => prev.some(p => p.id === approval.id) ? prev : [approval, ...prev])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveEvents[0]])

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [])

  async function toggleDetails(id: string) {
    const willOpen = !detailsOpen[id]
    setDetailsOpen(o => ({ ...o, [id]: willOpen }))
    if (willOpen && !details[id]) {
      setDetailsLoading(l => ({ ...l, [id]: true }))
      try {
        const d = await fetchJson<Detail>(`/news/detail/${id}`)
        setDetails(prev => ({ ...prev, [id]: d }))
      } catch { /* leave panel empty on failure */ }
      finally {
        setDetailsLoading(l => ({ ...l, [id]: false }))
      }
    }
  }

  async function resolve(id: string, action: 'approve' | 'reject') {
    setBusy(b => ({ ...b, [id]: true }))
    try {
      const res = await postJson<{ status: string; reason?: string; quantity?: number }>(`/news/${action}/${id}`, {})
      setResults(r => ({ ...r, [id]: res.status === 'placed'
        ? `Bought ${res.quantity} shares`
        : res.status === 'rejected' && action === 'reject'
          ? 'Dismissed'
          : `${res.status}${res.reason ? `: ${res.reason}` : ''}` }))
    } catch {
      setResults(r => ({ ...r, [id]: 'Failed to reach server' }))
    } finally {
      setBusy(b => ({ ...b, [id]: false }))
      setTimeout(() => setPending(prev => prev.filter(p => p.id !== id)), 2000)
    }
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="relative flex items-center gap-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 text-xs font-medium text-gray-200 rounded-lg px-3 py-1.5"
      >
        🔔 Buy Suggestions
        {pending.length > 0 && (
          <span className="bg-green-500 text-white text-[10px] font-bold rounded-full w-5 h-5 flex items-center justify-center">
            {pending.length}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 mt-2 flex flex-col gap-2 w-96 z-50">
          {pending.length === 0 && (
            <div className="card text-xs text-gray-500">No buy suggestions right now.</div>
          )}
          {pending.map(item => (
            <div key={item.id} className="card border border-brand-500/40 shadow-lg">
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-semibold text-green-400">Buy suggestion — {item.symbol}</span>
                <span className="text-[10px] text-gray-500">{item.source}</span>
              </div>
              <div className="text-xs text-gray-400 mb-1 line-clamp-2">{item.headline}</div>
              <div className="text-xs text-gray-300 mb-2">{item.rationale}</div>

              <button
                onClick={() => toggleDetails(item.id)}
                className="text-[11px] text-brand-500 hover:underline mb-1"
              >
                {detailsOpen[item.id] ? '▲ Hide details' : 'ℹ️ Why / current price / target…'}
              </button>

              {detailsOpen[item.id] && (
                detailsLoading[item.id]
                  ? <div className="text-xs text-gray-500 mt-1">Loading…</div>
                  : details[item.id]
                    ? <DetailPanel d={details[item.id]} />
                    : <div className="text-xs text-gray-500 mt-1">Couldn't load details.</div>
              )}

              {results[item.id] ? (
                <div className="text-xs text-gray-400 mt-2">{results[item.id]}</div>
              ) : (
                <div className="flex gap-2 mt-2">
                  <button
                    disabled={busy[item.id]}
                    onClick={() => resolve(item.id, 'approve')}
                    className={cn('flex-1 bg-green-600 hover:bg-green-500 disabled:opacity-50',
                                  'text-white text-xs font-medium rounded-md py-1.5')}
                  >
                    Approve
                  </button>
                  <button
                    disabled={busy[item.id]}
                    onClick={() => resolve(item.id, 'reject')}
                    className={cn('flex-1 bg-gray-700 hover:bg-gray-600 disabled:opacity-50',
                                  'text-white text-xs font-medium rounded-md py-1.5')}
                  >
                    Reject
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
