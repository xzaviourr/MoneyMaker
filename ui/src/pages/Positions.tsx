import { useQuery } from '@tanstack/react-query'
import { fetchJson, type Position } from '../lib/api'
import { fmtInr, fmt, cn } from '../lib/utils'
import { TableSkeleton } from '../components/Skeleton'
import { useStore } from '../hooks/useStore'

export default function PositionsPage() {
  const selectedSymbol    = useStore(s => s.selectedSymbol)
  const setSelectedSymbol = useStore(s => s.setSelectedSymbol)
  const selectedPortfolioId = useStore(s => s.selectedPortfolioId)
  const { data: positions = [], isLoading } = useQuery<Position[]>({
    queryKey:        ['positions', selectedPortfolioId],
    queryFn:         () => fetchJson('/portfolio/positions'),
    refetchInterval: 10_000,
  })

  const totalPnl = positions.reduce((s, p) => s + parseFloat(p.unrealized_pnl || '0'), 0)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Open Positions ({positions.length})</h1>
        <div className="text-sm">
          <span className="text-gray-500">Unrealised P&L </span>
          <span className={cn('font-mono font-semibold', totalPnl >= 0 ? 'pnl-pos' : 'pnl-neg')}>
            {totalPnl >= 0 ? '+' : ''}{fmtInr(totalPnl)}
          </span>
        </div>
      </div>

      {isLoading && <TableSkeleton rows={4} cols={7} />}

      <div className="card overflow-x-auto p-0">
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-gray-800 text-xs text-gray-500 uppercase tracking-wider">
              <th className="px-4 py-3">Symbol</th>
              <th className="px-4 py-3">Exchange</th>
              <th className="px-4 py-3">Side</th>
              <th className="px-4 py-3">Qty</th>
              <th className="px-4 py-3">Avg Price</th>
              <th className="px-4 py-3">LTP</th>
              <th className="px-4 py-3">Stop Loss</th>
              <th className="px-4 py-3">Target</th>
              <th className="px-4 py-3">Unrealised P&L</th>
              <th className="px-4 py-3">%</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p, i) => {
              const pnl    = parseFloat(p.unrealized_pnl || '0')
              const pnlPct = p.unrealized_pnl_pct ?? 0
              return (
                <tr key={i} className={cn('border-b border-gray-800 hover:bg-gray-900 transition-colors', selectedSymbol === p.symbol && 'bg-brand-900/20')}>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => setSelectedSymbol(selectedSymbol === p.symbol ? null : p.symbol)}
                      className={cn(
                        'font-mono font-semibold text-sm transition-colors text-left underline decoration-dotted underline-offset-2',
                        selectedSymbol === p.symbol
                          ? 'text-brand-500 decoration-brand-500'
                          : 'text-white decoration-gray-600 hover:text-brand-400 hover:decoration-brand-400'
                      )}
                    >
                      {p.symbol}
                    </button>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-400">{p.exchange}</td>
                  <td className="px-4 py-3">
                    <span className={cn('badge', p.side === 'BUY' ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300')}>
                      {p.side}
                    </span>
                  </td>
                  <td className="px-4 py-3 font-mono text-sm">{p.quantity}</td>
                  <td className="px-4 py-3 font-mono text-sm">{fmtInr(parseFloat(p.average_price))}</td>
                  <td className="px-4 py-3 font-mono text-sm">
                    {p.current_price ? fmtInr(parseFloat(p.current_price)) : '—'}
                  </td>
                  <td className="px-4 py-3 font-mono text-sm text-red-400">
                    {p.stop_loss ? fmtInr(parseFloat(p.stop_loss)) : '—'}
                  </td>
                  <td className="px-4 py-3 font-mono text-sm text-green-400">
                    {p.take_profit ? fmtInr(parseFloat(p.take_profit)) : '—'}
                  </td>
                  <td className={cn('px-4 py-3 font-mono text-sm', pnl >= 0 ? 'pnl-pos' : 'pnl-neg')}>
                    {pnl >= 0 ? '+' : ''}{fmtInr(pnl)}
                  </td>
                  <td className={cn('px-4 py-3 font-mono text-sm', pnlPct >= 0 ? 'pnl-pos' : 'pnl-neg')}>
                    {pnlPct >= 0 ? '+' : ''}{fmt(pnlPct, 2)}%
                  </td>
                </tr>
              )
            })}
            {!isLoading && positions.length === 0 && (
              <tr><td colSpan={10} className="px-4 py-8 text-center text-gray-600">No open positions</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
