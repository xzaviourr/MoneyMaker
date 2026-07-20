import { useQuery, useMutation } from '@tanstack/react-query'
import { fetchJson, postJson, type FeedbackSummary } from '../lib/api'
import { cn } from '../lib/utils'
import { CardSkeleton } from '../components/Skeleton'
import { useStore } from '../hooks/useStore'

interface StratStat {
  strategy:   string
  total:      number
  win_rate:   number
  expectancy: number
  sharpe:     number
}

interface AgentW {
  agent_id:      string
  current_weight: number
  accuracy:       number
  total_votes:    number
}

export default function FeedbackPage() {
  const selectedPortfolioId = useStore(s => s.selectedPortfolioId)
  const { data, isLoading } = useQuery<FeedbackSummary>({
    queryKey: ['feedback', selectedPortfolioId],
    queryFn:  () => fetchJson('/feedback/summary'),
  })

  const review = useMutation({
    mutationFn: () => postJson('/feedback/review', {}),
  })

  const strategies = (data?.strategies as StratStat[] | undefined) ?? []
  const weights = Object.values(data?.agent_weights ?? {}) as AgentW[]

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Feedback & Learning</h1>
        <button className="btn-primary" onClick={() => review.mutate()} disabled={review.isPending}>
          {review.isPending ? 'Running…' : 'Run Weekly Review'}
        </button>
      </div>

      {isLoading && <CardSkeleton lines={3} />}

      <div className="grid grid-cols-2 gap-6">
        {/* Strategy performance */}
        <div className="card">
          <div className="text-sm font-semibold mb-3">Strategy Performance</div>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-500 border-b border-gray-800">
                <th className="text-left pb-2">Strategy</th>
                <th className="text-right pb-2">Trades</th>
                <th className="text-right pb-2">Win%</th>
                <th className="text-right pb-2">Sharpe</th>
              </tr>
            </thead>
            <tbody>
              {strategies.map(s => (
                <tr key={s.strategy} className="border-b border-gray-800">
                  <td className="py-1.5 font-mono">{s.strategy}</td>
                  <td className="py-1.5 text-right">{s.total}</td>
                  <td className={cn('py-1.5 text-right font-mono', s.win_rate >= 0.5 ? 'text-green-400' : 'text-red-400')}>
                    {(s.win_rate * 100).toFixed(1)}%
                  </td>
                  <td className={cn('py-1.5 text-right font-mono', s.sharpe >= 0.5 ? 'text-green-400' : 'text-red-400')}>
                    {s.sharpe.toFixed(2)}
                  </td>
                </tr>
              ))}
              {strategies.length === 0 && (
                <tr><td colSpan={4} className="py-4 text-center text-gray-600">No data yet</td></tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Agent weights */}
        <div className="card">
          <div className="text-sm font-semibold mb-3">Agent Calibration Weights</div>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-500 border-b border-gray-800">
                <th className="text-left pb-2">Agent</th>
                <th className="text-right pb-2">Weight</th>
                <th className="text-right pb-2">Accuracy</th>
                <th className="text-right pb-2">Votes</th>
              </tr>
            </thead>
            <tbody>
              {weights.map(w => (
                <tr key={w.agent_id} className="border-b border-gray-800">
                  <td className="py-1.5 font-mono">{w.agent_id}</td>
                  <td className={cn('py-1.5 text-right font-mono', w.current_weight >= 1 ? 'text-green-400' : 'text-yellow-400')}>
                    {w.current_weight.toFixed(2)}×
                  </td>
                  <td className="py-1.5 text-right">{(w.accuracy * 100).toFixed(1)}%</td>
                  <td className="py-1.5 text-right">{w.total_votes}</td>
                </tr>
              ))}
              {weights.length === 0 && (
                <tr><td colSpan={4} className="py-4 text-center text-gray-600">No calibration data yet</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {data?.weakest && data.weakest.length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-2 text-yellow-400">⚠ Weakest Strategies</div>
          <div className="flex gap-2 flex-wrap">
            {data.weakest.map((s: string) => (
              <span key={s} className="badge bg-yellow-900 text-yellow-300">{s}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
