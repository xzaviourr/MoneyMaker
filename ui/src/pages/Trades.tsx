import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchJson, type Trade } from '../lib/api'
import { fmtInr, fmtPrice, cn } from '../lib/utils'

// How long a position was actually held, from the original buy to this sell —
// not shown anywhere before, since the trade book only stored the closing
// timestamp with no link back to when it was opened.
function holdingDuration(entryTime: string | null, closeTime: string): string {
  if (!entryTime) return '—'
  const ms = new Date(closeTime).getTime() - new Date(entryTime).getTime()
  if (!Number.isFinite(ms) || ms < 0) return '—'
  const mins = Math.round(ms / 60000)
  if (mins < 60) return `${mins}m`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ${mins % 60}m`
  const days = Math.floor(hours / 24)
  return `${days}d ${hours % 24}h`
}

export default function TradesPage() {
  const [sourceFilter, setSourceFilter] = useState('all')
  const { data: allTrades = [], isLoading } = useQuery<Trade[]>({
    queryKey: ['trades'],
    queryFn:  () => fetchJson('/portfolio/trades'),
    refetchInterval: 5000,
  })

  const sources = Array.from(new Set(allTrades.map(t => t.source_desk || t.source_pod).filter(Boolean))) as string[]
  const trades = sourceFilter === 'all'
    ? allTrades
    : allTrades.filter(t => (t.source_desk || t.source_pod) === sourceFilter)

  const closed = trades.filter(t => t.entry_price != null)
  const totalRealised = closed.reduce((sum, t) => sum + t.pnl, 0)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Trade Book ({closed.length} closed)</h1>
        <div className="flex items-center gap-4 text-sm">
          <select
            className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-brand-500"
            value={sourceFilter}
            onChange={e => setSourceFilter(e.target.value)}
          >
            <option value="all">All pods/desks</option>
            {sources.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <span className="text-gray-500">Total Realised P&L </span>
          <span className={cn('font-mono font-semibold', totalRealised >= 0 ? 'pnl-pos' : 'pnl-neg')}>
            {totalRealised >= 0 ? '+' : ''}{fmtInr(totalRealised)}
          </span>
        </div>
      </div>

      {isLoading && <div className="text-gray-500 text-sm">Loading…</div>}

      <div className="card overflow-x-auto p-0">
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-gray-800 text-xs text-gray-500 uppercase tracking-wider">
              <th className="px-4 py-3">Time</th>
              <th className="px-4 py-3">Symbol</th>
              <th className="px-4 py-3">Side</th>
              <th className="px-4 py-3">Qty</th>
              <th className="px-4 py-3">Bought At</th>
              <th className="px-4 py-3">Closed At</th>
              <th className="px-4 py-3">Held For</th>
              <th className="px-4 py-3">P&L</th>
              <th className="px-4 py-3">Pod / Desk</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t, i) => (
              <tr key={t.trade_id ?? i} className="border-b border-gray-800 hover:bg-gray-900 transition-colors">
                <td className="px-4 py-3 text-xs text-gray-500 font-mono">{t.timestamp?.slice(0, 19).replace('T', ' ')}</td>
                <td className="px-4 py-3 font-mono font-semibold text-sm">{t.symbol}</td>
                <td className="px-4 py-3">
                  <span className={cn('badge', t.side === 'buy' ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300')}>
                    {t.side.toUpperCase()}
                  </span>
                </td>
                <td className="px-4 py-3 font-mono text-sm">{t.quantity}</td>
                <td className="px-4 py-3 font-mono text-sm">
                  {t.entry_price != null ? fmtPrice(t.entry_price) : (t.side === 'buy' ? fmtPrice(t.price) : '—')}
                </td>
                <td className="px-4 py-3 font-mono text-sm">
                  {t.entry_price != null ? fmtPrice(t.price) : '— still open —'}
                </td>
                <td className="px-4 py-3 font-mono text-sm text-gray-400">
                  {t.entry_price != null ? holdingDuration(t.entry_time, t.timestamp) : '—'}
                </td>
                <td className={cn('px-4 py-3 font-mono text-sm font-semibold', t.pnl >= 0 ? 'pnl-pos' : 'pnl-neg')}>
                  {t.entry_price != null ? `${t.pnl >= 0 ? '+' : ''}${fmtPrice(t.pnl)}` : '—'}
                </td>
                <td className="px-4 py-3 text-xs text-gray-400">{t.source_pod || t.source_desk || '—'}</td>
              </tr>
            ))}
            {!isLoading && trades.length === 0 && (
              <tr><td colSpan={9} className="px-4 py-8 text-center text-gray-600">No trades yet</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
