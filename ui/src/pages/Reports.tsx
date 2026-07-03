import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchJson, type Trade } from '../lib/api'
import { fmtInr, fmtPrice, cn } from '../lib/utils'

// ── period helpers ────────────────────────────────────────────────────────────

type Period = 'today' | 'week' | 'month' | 'all'

function periodStart(p: Period): Date | null {
  const now = new Date()
  if (p === 'today') return new Date(now.getFullYear(), now.getMonth(), now.getDate())
  if (p === 'week') {
    const d = new Date(now); d.setDate(d.getDate() - 6); d.setHours(0, 0, 0, 0); return d
  }
  if (p === 'month') return new Date(now.getFullYear(), now.getMonth(), 1)
  return null
}

function inPeriod(t: Trade, start: Date | null): boolean {
  if (!start) return true
  return new Date(t.timestamp) >= start
}

function fmtTs(iso: string) {
  return new Date(iso).toLocaleString('en-IN', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

// ── stat card ─────────────────────────────────────────────────────────────────

function StatCard({ label, value, sub, color }: {
  label: string; value: string; sub?: string; color?: string
}) {
  return (
    <div className="card py-4 text-center space-y-1">
      <div className={cn('text-2xl font-bold font-mono', color ?? 'text-white')}>{value}</div>
      {sub && <div className="text-xs text-gray-500">{sub}</div>}
      <div className="text-xs text-gray-500 mt-0.5">{label}</div>
    </div>
  )
}

// ── page ──────────────────────────────────────────────────────────────────────

export default function ReportsPage() {
  const [period, setPeriod] = useState<Period>('today')

  const { data: allTrades = [], isLoading } = useQuery<Trade[]>({
    queryKey:        ['trades'],
    queryFn:         () => fetchJson('/portfolio/trades'),
    refetchInterval: 30_000,
  })

  const start  = periodStart(period)
  const trades = useMemo(() => allTrades.filter(t => inPeriod(t, start)), [allTrades, start])

  // only closed trades have pnl and entry_price
  const closed  = trades.filter(t => t.entry_price != null)
  const winners = closed.filter(t => t.pnl > 0)
  const losers  = closed.filter(t => t.pnl < 0)

  const totalPnl  = closed.reduce((s, t) => s + t.pnl, 0)
  const winRate   = closed.length ? Math.round((winners.length / closed.length) * 100) : 0
  const avgPnl    = closed.length ? totalPnl / closed.length : 0
  const bestTrade = closed.length ? closed.reduce((a, b) => a.pnl > b.pnl ? a : b) : null
  const worstTrade= closed.length ? closed.reduce((a, b) => a.pnl < b.pnl ? a : b) : null

  // per-symbol breakdown
  const bySymbol = useMemo(() => {
    const map: Record<string, { symbol: string; trades: number; buys: number; sells: number; pnl: number; wins: number }> = {}
    for (const t of closed) {
      if (!map[t.symbol]) map[t.symbol] = { symbol: t.symbol, trades: 0, buys: 0, sells: 0, pnl: 0, wins: 0 }
      map[t.symbol].trades++
      if (t.side === 'buy') map[t.symbol].buys++
      else map[t.symbol].sells++
      map[t.symbol].pnl += t.pnl
      if (t.pnl > 0) map[t.symbol].wins++
    }
    return Object.values(map).sort((a, b) => b.pnl - a.pnl)
  }, [closed])

  // per-source breakdown
  const bySource = useMemo(() => {
    const map: Record<string, { source: string; trades: number; pnl: number }> = {}
    for (const t of closed) {
      const src = t.source_desk || t.source_pod || 'unknown'
      if (!map[src]) map[src] = { source: src, trades: 0, pnl: 0 }
      map[src].trades++
      map[src].pnl += t.pnl
    }
    return Object.values(map).sort((a, b) => b.pnl - a.pnl)
  }, [closed])

  const PERIODS: { key: Period; label: string }[] = [
    { key: 'today', label: 'Today' },
    { key: 'week',  label: 'Last 7 Days' },
    { key: 'month', label: 'This Month' },
    { key: 'all',   label: 'All Time' },
  ]

  return (
    <div className="space-y-8">

      {/* ── header ───────────────────────────────────────── */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold">Trade Report</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Summary of all buys and sells — P&L, win rate, and per-stock breakdown.
          </p>
        </div>

        <div className="flex rounded-lg overflow-hidden border border-gray-700 text-sm">
          {PERIODS.map(p => (
            <button
              key={p.key}
              onClick={() => setPeriod(p.key)}
              className={cn(
                'px-4 py-2 font-medium',
                period === p.key ? 'bg-brand-500 text-white' : 'bg-gray-900 text-gray-400 hover:text-white',
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {isLoading && <div className="text-gray-500 text-sm">Loading…</div>}

      {/* ── summary stats ─────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <StatCard
          label="Realised P&L"
          value={`${totalPnl >= 0 ? '+' : ''}${fmtInr(totalPnl)}`}
          color={totalPnl >= 0 ? 'pnl-pos' : 'pnl-neg'}
        />
        <StatCard label="Closed Trades"  value={String(closed.length)}  sub={`${trades.length} total`} />
        <StatCard
          label="Win Rate"
          value={closed.length ? `${winRate}%` : '—'}
          sub={closed.length ? `${winners.length}W / ${losers.length}L` : undefined}
          color={winRate >= 50 ? 'text-green-400' : 'text-red-400'}
        />
        <StatCard
          label="Avg Trade P&L"
          value={closed.length ? `${avgPnl >= 0 ? '+' : ''}${fmtInr(avgPnl)}` : '—'}
          color={avgPnl >= 0 ? 'pnl-pos' : 'pnl-neg'}
        />
        <StatCard
          label="Best Trade"
          value={bestTrade ? `${bestTrade.pnl >= 0 ? '+' : ''}${fmtInr(bestTrade.pnl)}` : '—'}
          sub={bestTrade?.symbol}
          color={bestTrade && bestTrade.pnl >= 0 ? 'pnl-pos' : 'pnl-neg'}
        />
        <StatCard
          label="Worst Trade"
          value={worstTrade ? fmtInr(worstTrade.pnl) : '—'}
          sub={worstTrade?.symbol}
          color="pnl-neg"
        />
      </div>

      {closed.length === 0 && !isLoading && (
        <div className="card text-center text-gray-600 py-10">
          No closed trades in this period.
        </div>
      )}

      {closed.length > 0 && (
        <>
          {/* ── per-symbol breakdown ───────────────────────── */}
          <div className="space-y-3">
            <h2 className="text-base font-semibold">P&L by Stock</h2>
            <div className="card overflow-x-auto p-0">
              <table className="w-full text-left">
                <thead>
                  <tr className="border-b border-gray-800 text-xs text-gray-500 uppercase tracking-wider">
                    <th className="px-4 py-3">Symbol</th>
                    <th className="px-4 py-3">Trades</th>
                    <th className="px-4 py-3">Win Rate</th>
                    <th className="px-4 py-3">Net P&L</th>
                    <th className="px-4 py-3">Avg P&L / trade</th>
                  </tr>
                </thead>
                <tbody>
                  {bySymbol.map(row => {
                    const wr  = Math.round((row.wins / row.trades) * 100)
                    const avg = row.pnl / row.trades
                    return (
                      <tr key={row.symbol} className="border-b border-gray-800 hover:bg-gray-900">
                        <td className="px-4 py-3 font-mono font-semibold text-sm">{row.symbol}</td>
                        <td className="px-4 py-3 text-sm text-gray-400">{row.trades}</td>
                        <td className="px-4 py-3 text-sm">
                          <span className={cn('font-semibold', wr >= 50 ? 'text-green-400' : 'text-red-400')}>
                            {wr}%
                          </span>
                          <span className="text-gray-600 text-xs ml-1">({row.wins}W)</span>
                        </td>
                        <td className={cn('px-4 py-3 font-mono font-semibold text-sm', row.pnl >= 0 ? 'pnl-pos' : 'pnl-neg')}>
                          {row.pnl >= 0 ? '+' : ''}{fmtPrice(row.pnl)}
                        </td>
                        <td className={cn('px-4 py-3 font-mono text-sm', avg >= 0 ? 'pnl-pos' : 'pnl-neg')}>
                          {avg >= 0 ? '+' : ''}{fmtPrice(avg)}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* ── per-source breakdown ───────────────────────── */}
          {bySource.length > 1 && (
            <div className="space-y-3">
              <h2 className="text-base font-semibold">P&L by Strategy / Desk</h2>
              <div className="card overflow-x-auto p-0">
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-gray-800 text-xs text-gray-500 uppercase tracking-wider">
                      <th className="px-4 py-3">Source</th>
                      <th className="px-4 py-3">Trades</th>
                      <th className="px-4 py-3">Net P&L</th>
                      <th className="px-4 py-3">Avg P&L / trade</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bySource.map(row => {
                      const avg = row.pnl / row.trades
                      return (
                        <tr key={row.source} className="border-b border-gray-800 hover:bg-gray-900">
                          <td className="px-4 py-3 text-sm text-gray-300">{row.source.replace(/_/g, ' ')}</td>
                          <td className="px-4 py-3 text-sm text-gray-400">{row.trades}</td>
                          <td className={cn('px-4 py-3 font-mono font-semibold text-sm', row.pnl >= 0 ? 'pnl-pos' : 'pnl-neg')}>
                            {row.pnl >= 0 ? '+' : ''}{fmtPrice(row.pnl)}
                          </td>
                          <td className={cn('px-4 py-3 font-mono text-sm', avg >= 0 ? 'pnl-pos' : 'pnl-neg')}>
                            {avg >= 0 ? '+' : ''}{fmtPrice(avg)}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* ── full trade list ────────────────────────────── */}
          <div className="space-y-3">
            <h2 className="text-base font-semibold">
              All Trades
              <span className="ml-2 text-xs font-normal text-gray-500">{closed.length} closed</span>
            </h2>
            <div className="card overflow-x-auto p-0">
              <table className="w-full text-left">
                <thead>
                  <tr className="border-b border-gray-800 text-xs text-gray-500 uppercase tracking-wider">
                    <th className="px-4 py-3">Time</th>
                    <th className="px-4 py-3">Symbol</th>
                    <th className="px-4 py-3">Side</th>
                    <th className="px-4 py-3">Qty</th>
                    <th className="px-4 py-3">Entry</th>
                    <th className="px-4 py-3">Exit</th>
                    <th className="px-4 py-3">P&L</th>
                    <th className="px-4 py-3">Source</th>
                  </tr>
                </thead>
                <tbody>
                  {closed.map((t, i) => (
                    <tr key={t.trade_id ?? i} className="border-b border-gray-800 hover:bg-gray-900">
                      <td className="px-4 py-3 text-xs text-gray-500 font-mono">{fmtTs(t.timestamp)}</td>
                      <td className="px-4 py-3 font-mono font-semibold text-sm">{t.symbol}</td>
                      <td className="px-4 py-3">
                        <span className={cn('badge', t.side === 'buy' ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300')}>
                          {t.side.toUpperCase()}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm font-mono">{t.quantity}</td>
                      <td className="px-4 py-3 text-sm font-mono text-gray-400">{fmtPrice(t.entry_price)}</td>
                      <td className="px-4 py-3 text-sm font-mono text-gray-300">{fmtPrice(t.price)}</td>
                      <td className={cn('px-4 py-3 text-sm font-mono font-semibold', t.pnl >= 0 ? 'pnl-pos' : 'pnl-neg')}>
                        {t.pnl >= 0 ? '+' : ''}{fmtPrice(t.pnl)}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-500">{t.source_desk || t.source_pod || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

    </div>
  )
}
