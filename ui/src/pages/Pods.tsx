import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchJson, postJson, type PodInfo } from '../lib/api'
import { fmtInr, cn } from '../lib/utils'
import { TableSkeleton } from '../components/Skeleton'

const STATE_COLORS: Record<string, string> = {
  SANDBOX:   'bg-gray-700 text-gray-300',
  PROBATION: 'bg-yellow-900 text-yellow-300',
  LIVE:      'bg-green-900 text-green-300',
  REVIEW:    'bg-orange-900 text-orange-300',
  KILLED:    'bg-red-900 text-red-300',
  PAUSED:    'bg-blue-900 text-blue-300',
}

function PodRow({ pod, onCommand }: { pod: PodInfo; onCommand: (id: string, action: string) => void }) {
  const m = pod.metrics as Record<string, number>
  return (
    <tr className="border-b border-gray-800 hover:bg-gray-900 transition-colors">
      <td className="px-4 py-3 font-mono text-sm">{pod.pod_id}</td>
      <td className="px-4 py-3 text-sm">{pod.name}</td>
      <td className="px-4 py-3">
        <span className={cn('badge', STATE_COLORS[pod.state] || 'bg-gray-800 text-gray-400')}>
          {pod.state}
        </span>
      </td>
      <td className="px-4 py-3 font-mono text-sm">{pod.capital_budget ? fmtInr(pod.capital_budget) : '—'}</td>
      <td className="px-4 py-3 font-mono text-sm">{m?.total_trades ?? '—'}</td>
      <td className="px-4 py-3 font-mono text-sm">
        <span className={cn((m?.win_rate ?? 0) >= 0.5 ? 'text-green-400' : 'text-red-400')}>
          {m?.win_rate != null ? `${(m.win_rate * 100).toFixed(1)}%` : '—'}
        </span>
      </td>
      <td className={cn('px-4 py-3 font-mono text-sm', (m?.total_pnl ?? 0) >= 0 ? 'pnl-pos' : 'pnl-neg')}>
        {m?.total_pnl != null ? `${m.total_pnl >= 0 ? '+' : ''}${fmtInr(m.total_pnl)}` : '—'}
      </td>
      <td className="px-4 py-3 font-mono text-sm">
        {m?.sharpe_ratio != null ? m.sharpe_ratio.toFixed(2) : '—'}
      </td>
      <td className="px-4 py-3">
        <div className="flex gap-1">
          {pod.state !== 'PAUSED' && pod.state !== 'KILLED' && (
            <button className="btn-ghost text-xs px-2 py-1"
              aria-label={`Pause ${pod.pod_id}`}
              onClick={() => onCommand(pod.pod_id, 'pause')}>Pause</button>
          )}
          {pod.state === 'PAUSED' && (
            <button className="btn-primary text-xs px-2 py-1"
              aria-label={`Resume ${pod.pod_id}`}
              onClick={() => onCommand(pod.pod_id, 'resume')}>Resume</button>
          )}
          {pod.state !== 'KILLED' && (
            <button className="btn-danger text-xs px-2 py-1"
              aria-label={`Kill ${pod.pod_id}`}
              onClick={() => onCommand(pod.pod_id, 'kill')}>Kill</button>
          )}
        </div>
      </td>
    </tr>
  )
}

export default function PodsPage() {
  const qc = useQueryClient()
  const { data: pods = [], isLoading } = useQuery<PodInfo[]>({
    queryKey: ['pods'],
    queryFn:  () => fetchJson('/pods/'),
  })

  const cmd = useMutation({
    mutationFn: ({ id, action }: { id: string; action: string }) =>
      postJson(`/pods/${id}/command`, { action }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pods'] }),
  })

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Intraday Pods</h1>
      {isLoading && <TableSkeleton rows={4} cols={9} />}
      <div className="card overflow-x-auto p-0">
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-gray-800 text-xs text-gray-500 uppercase tracking-wider">
              <th className="px-4 py-3">ID</th>
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">State</th>
              <th className="px-4 py-3">Fund Allotted</th>
              <th className="px-4 py-3">Trades</th>
              <th className="px-4 py-3">Win Rate</th>
              <th className="px-4 py-3">Running P&L</th>
              <th className="px-4 py-3">Sharpe</th>
              <th className="px-4 py-3">Actions</th>
            </tr>
          </thead>
          <tbody>
            {pods.map(p => (
              <PodRow key={p.pod_id} pod={p}
                onCommand={(id, action) => cmd.mutate({ id, action })} />
            ))}
            {!isLoading && pods.length === 0 && (
              <tr><td colSpan={9} className="px-4 py-8 text-center text-gray-600">No pods running</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
