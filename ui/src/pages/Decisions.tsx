import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchJson, type Decision } from '../lib/api'
import { cn } from '../lib/utils'

function parseJson(raw: string | null): Record<string, unknown> | null {
  if (!raw) return null
  try { return JSON.parse(raw) } catch { return null }
}

function num(v: unknown): number | null {
  const n = typeof v === 'string' ? parseFloat(v) : typeof v === 'number' ? v : NaN
  return Number.isFinite(n) ? n : null
}

export default function DecisionsPage() {
  const [symbol, setSymbol]   = useState('')
  const [agentId, setAgentId] = useState('')

  const params = new URLSearchParams()
  if (symbol)  params.set('symbol',   symbol)
  if (agentId) params.set('agent_id', agentId)

  const { data: decisions = [], isLoading } = useQuery<Decision[]>({
    queryKey: ['decisions', symbol, agentId],
    queryFn:  () => fetchJson(`/decisions/?${params}`),
  })

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Agent Decisions</h1>

      <div className="flex gap-3">
        <input
          className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white placeholder:text-gray-600 focus:outline-none focus:border-brand-500"
          placeholder="Filter by symbol…"
          value={symbol}
          onChange={e => setSymbol(e.target.value.toUpperCase())}
        />
        <input
          className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white placeholder:text-gray-600 focus:outline-none focus:border-brand-500"
          placeholder="Filter by agent…"
          value={agentId}
          onChange={e => setAgentId(e.target.value)}
        />
      </div>

      {isLoading && <div className="text-gray-500 text-sm">Loading…</div>}

      <div className="space-y-2">
        {decisions.map((d, i) => {
          const outputs = parseJson(d.outputs)
          const qty   = num(outputs?.quantity)
          const price = num(outputs?.fill_price)
          const value = qty != null && price != null ? qty * price : null

          return (
            <div key={i} className="card flex flex-col gap-1">
              <div className="flex items-center gap-3">
                <span className="text-xs font-mono text-gray-500">{d.event_ts?.slice(0, 19).replace('T', ' ')}</span>
                <span className="text-xs font-semibold text-brand-500">{d.agent_id}</span>
                {d.symbol && (
                  <span className="badge bg-gray-800 text-gray-300">{d.symbol}</span>
                )}
                {value != null && (
                  <span className="badge bg-emerald-900 text-emerald-300">
                    ₹{value.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                    {qty != null && price != null && ` (${qty} × ₹${price.toLocaleString('en-IN')})`}
                  </span>
                )}
                <span className="ml-auto text-xs font-mono text-yellow-400">{d.decision}</span>
                {d.outcome && (
                  <span className={cn('badge', d.outcome.includes('+') || d.outcome === 'correct'
                    ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300')}>
                    {d.outcome}
                  </span>
                )}
              </div>
              {d.reasoning && (
                <p className="text-xs text-gray-400 mt-1">{d.reasoning.slice(0, 200)}</p>
              )}
            </div>
          )
        })}
        {!isLoading && decisions.length === 0 && (
          <div className="text-center text-gray-600 py-8">No decisions recorded yet</div>
        )}
      </div>
    </div>
  )
}
