import { useQuery } from '@tanstack/react-query'
import { fetchJson, type QueueResponse, type Decision } from '../lib/api'
import { cn } from '../lib/utils'
import { CardSkeleton } from '../components/Skeleton'
import { useStore } from '../hooks/useStore'

function fmtTs(iso: string | null | undefined): string {
  if (!iso) return '—'
  const utc = iso.endsWith('Z') ? iso : iso + 'Z'
  return new Date(utc).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function agentLabel(agentId: string): string {
  if (agentId.includes('opportunity_scout')) return 'Scout'
  if (agentId.includes('bull_advocate'))     return 'Bull'
  if (agentId.includes('bear_advocate'))     return 'Bear'
  if (agentId.includes('devils_advocate'))   return "Devil's Advocate"
  if (agentId.includes('sector_specialist')) return 'Sector'
  if (agentId.includes('momentum_analyst'))  return 'Momentum'
  if (agentId.includes('committee_chair'))   return 'Chair — Verdict'
  return agentId
}

export default function QueuePage() {
  const selectedPortfolioId = useStore(s => s.selectedPortfolioId)

  const { data: queue, isLoading: qLoading } = useQuery<QueueResponse>({
    queryKey:        ['queue', selectedPortfolioId],
    queryFn:         () => fetchJson('/system/queue'),
    refetchInterval: 5000,
  })

  const { data: recent = [], isLoading: dLoading } = useQuery<Decision[]>({
    queryKey:        ['queue-recent-decisions', selectedPortfolioId],
    queryFn:         () => fetchJson('/decisions/?limit=60'),
    refetchInterval: 5000,
  })

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Live Queue &amp; Activity</h1>
        <p className="text-sm text-gray-500 mt-1">
          What's currently lined up for debate, what each intraday pod is watching, and exactly
          when each stage of a debate happened — for your own analysis, not just the end result.
        </p>
      </div>

      {qLoading && <CardSkeleton lines={3} />}

      {/* ── Long-Term Desk queue ────────────────────────────────────────── */}
      <div className="card">
        <div className="flex items-center justify-between mb-3">
          <div className="text-sm font-semibold">Long-Term Desk — Waiting for a Debate</div>
          <span className="badge bg-brand-900 text-brand-300">{queue?.long_term_queue_size ?? 0} queued</span>
        </div>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-500 border-b border-gray-800">
              <th className="text-left pb-2">Symbol</th>
              <th className="text-left pb-2">Direction</th>
              <th className="text-right pb-2">Conviction</th>
              <th className="text-left pb-2">Supporting Strategies</th>
              <th className="text-right pb-2">Queued At</th>
              <th className="text-right pb-2">Expires</th>
            </tr>
          </thead>
          <tbody>
            {(queue?.long_term_queue ?? []).map((item, i) => (
              <tr key={i} className="border-b border-gray-800">
                <td className="py-1.5 font-mono font-semibold text-white">{item.symbol}</td>
                <td className={cn('py-1.5', item.direction === 'long' ? 'text-green-400' : 'text-red-400')}>
                  {item.direction.toUpperCase()}
                </td>
                <td className="py-1.5 text-right font-mono">{(item.conviction_score * 100).toFixed(0)}%</td>
                <td className="py-1.5 text-gray-500 truncate max-w-[220px]">{item.supporting_strategies.join(', ')}</td>
                <td className="py-1.5 text-right font-mono text-gray-500">{fmtTs(item.queued_at)}</td>
                <td className="py-1.5 text-right font-mono text-gray-600">{fmtTs(item.expires_at)}</td>
              </tr>
            ))}
            {(queue?.long_term_queue ?? []).length === 0 && (
              <tr><td colSpan={6} className="py-4 text-center text-gray-600">Nothing queued right now — next scan runs every 10 minutes</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* ── Intraday pods ───────────────────────────────────────────────── */}
      <div className="card">
        <div className="text-sm font-semibold mb-3">Intraday Pods — What Each One Is Watching</div>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-500 border-b border-gray-800">
              <th className="text-left pb-2">Pod</th>
              <th className="text-left pb-2">State</th>
              <th className="text-left pb-2">Watchlist</th>
              <th className="text-right pb-2">Open Positions</th>
              <th className="text-right pb-2">Trades Today</th>
            </tr>
          </thead>
          <tbody>
            {(queue?.intraday_pods ?? []).map(pod => (
              <tr key={pod.pod_id} className="border-b border-gray-800">
                <td className="py-1.5 font-semibold text-white">{pod.name}</td>
                <td className="py-1.5">
                  <span className={cn('badge', pod.state === 'LIVE' ? 'bg-green-900 text-green-300' : 'bg-gray-800 text-gray-400')}>
                    {pod.state}
                  </span>
                </td>
                <td className="py-1.5 text-gray-500 truncate max-w-[280px]">{pod.watchlist.join(', ')}</td>
                <td className="py-1.5 text-right font-mono">{pod.open_positions}</td>
                <td className="py-1.5 text-right font-mono">{pod.trades_today}</td>
              </tr>
            ))}
            {(queue?.intraday_pods ?? []).length === 0 && (
              <tr><td colSpan={5} className="py-4 text-center text-gray-600">No pod data yet</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* ── Live timing timeline ────────────────────────────────────────── */}
      <div className="card">
        <div className="text-sm font-semibold mb-3">Discussion Room — Exact Timing, Most Recent First</div>
        <div className="text-xs text-gray-500 mb-3">
          Every agent's turn, with the exact time it happened — this is the raw timing record
          behind every debate summary elsewhere in the app.
        </div>
        {dLoading && <CardSkeleton lines={3} />}
        <div className="space-y-1 max-h-[500px] overflow-y-auto">
          {recent.map((d, i) => (
            <div key={i} className="flex items-center gap-3 text-xs border-b border-gray-800 py-1.5">
              <span className="font-mono text-gray-600 w-20 shrink-0">{fmtTs(d.event_ts)}</span>
              <span className="font-mono font-semibold text-white w-20 shrink-0 truncate">{d.symbol ?? '—'}</span>
              <span className="text-brand-400 w-32 shrink-0 truncate">{agentLabel(d.agent_id)}</span>
              <span className="text-gray-500 truncate">{d.decision}</span>
            </div>
          ))}
          {!dLoading && recent.length === 0 && (
            <div className="py-4 text-center text-gray-600">No activity yet</div>
          )}
        </div>
      </div>
    </div>
  )
}
