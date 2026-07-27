import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchJson, postJson, type UserIdea } from '../lib/api'
import { cn } from '../lib/utils'
import { CardSkeleton } from '../components/Skeleton'
import { useStore } from '../hooks/useStore'

function fmtTs(iso: string | null | undefined): string {
  if (!iso) return '—'
  const utc = iso.endsWith('Z') ? iso : iso + 'Z'
  return new Date(utc).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

function statusBadge(status: UserIdea['status']) {
  const map: Record<UserIdea['status'], string> = {
    pending:  'bg-gray-800 text-gray-400',
    debated:  'bg-blue-900 text-blue-300',
    executed: 'bg-green-900 text-green-300',
    failed:   'bg-red-900 text-red-300',
  }
  return <span className={cn('badge', map[status])}>{status}</span>
}

function parseIssues(raw: string | null): string[] {
  if (!raw) return []
  try { return JSON.parse(raw) as string[] } catch { return [] }
}

export default function MyIdeasPage() {
  const selectedPortfolioId = useStore(s => s.selectedPortfolioId)
  const queryClient = useQueryClient()
  const [symbol, setSymbol] = useState('')
  const [note, setNote] = useState('')

  const { data: ideas = [], isLoading } = useQuery<UserIdea[]>({
    queryKey:        ['my-ideas', selectedPortfolioId],
    queryFn:         () => fetchJson('/ideas/'),
    refetchInterval: 4000,
  })

  const submit = useMutation({
    mutationFn: () => postJson('/ideas/', { symbol: symbol.trim().toUpperCase(), note }),
    onSuccess: () => {
      setSymbol('')
      setNote('')
      queryClient.invalidateQueries({ queryKey: ['my-ideas', selectedPortfolioId] })
    },
  })

  const execute = useMutation({
    mutationFn: (id: number) => postJson(`/ideas/${id}/execute`, {}),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['my-ideas', selectedPortfolioId] }),
  })

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">My Ideas</h1>
        <p className="text-sm text-gray-500 mt-1">
          Thought of a stock outside market hours? Submit it here — the AI runs its full debate
          on it (same as anything it finds on its own) so you can see the reasoning, but the
          verdict never blocks you. You decide whether to actually buy.
        </p>
      </div>

      {/* ── Submit form ────────────────────────────────────────────────── */}
      <div className="card">
        <div className="text-sm font-semibold mb-3">Submit a Stock</div>
        <div className="flex flex-col sm:flex-row gap-3">
          <input
            className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white placeholder:text-gray-600 focus:outline-none focus:border-brand-500 sm:w-40"
            placeholder="Symbol (e.g. RELIANCE)"
            value={symbol}
            autoComplete="off"
            onChange={e => setSymbol(e.target.value)}
          />
          <input
            className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white placeholder:text-gray-600 focus:outline-none focus:border-brand-500 flex-1"
            placeholder="Why? (optional — e.g. saw strong Q results on the news tonight)"
            value={note}
            autoComplete="off"
            onChange={e => setNote(e.target.value)}
          />
          <button
            className="btn-primary shrink-0"
            disabled={!symbol.trim() || submit.isPending}
            onClick={() => submit.mutate()}
          >
            {submit.isPending ? 'Submitting…' : 'Submit for Debate'}
          </button>
        </div>
        {submit.isError && (
          <div className="text-xs text-red-400 mt-2">Couldn't submit — check the symbol and try again.</div>
        )}
      </div>

      {/* ── Ideas list ─────────────────────────────────────────────────── */}
      {isLoading && <CardSkeleton lines={3} />}
      <div className="space-y-3">
        {ideas.map(idea => {
          const issues = parseIssues(idea.risk_issues)
          return (
            <div key={idea.id} className="card">
              <div className="flex items-start justify-between gap-3 mb-2">
                <div>
                  <span className="font-mono font-semibold text-white text-base">{idea.symbol}</span>
                  {idea.note && <div className="text-xs text-gray-500 mt-0.5">"{idea.note}"</div>}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {statusBadge(idea.status)}
                  <span className="text-xs text-gray-600">{fmtTs(idea.submitted_at)}</span>
                </div>
              </div>

              {idea.status === 'pending' && (
                <div className="text-xs text-gray-500 py-2">Debating — Scout → Bull → Bear → Devil's Advocate → Chair… usually under a minute.</div>
              )}

              {idea.status === 'failed' && (
                <div className="text-xs text-red-400 py-2">{idea.error || 'Something went wrong.'}</div>
              )}

              {(idea.status === 'debated' || idea.status === 'executed') && (
                <div className="space-y-2 mt-2">
                  <div className="flex items-center gap-2">
                    <span className={cn('badge', idea.verdict_approved ? 'bg-green-900 text-green-300' : 'bg-yellow-900 text-yellow-300')}>
                      AI verdict: {idea.verdict_approved ? 'would approve' : 'would reject'}
                    </span>
                    <span className="text-xs text-gray-500">conviction {((idea.chair_conviction ?? 0) * 100).toFixed(0)}%</span>
                    {idea.risk_passed === 0 && <span className="badge bg-yellow-900 text-yellow-300">sizing flagged</span>}
                  </div>
                  <div className="text-xs text-gray-400">{idea.verdict_reasoning}</div>
                  <div className="grid sm:grid-cols-2 gap-3 mt-2">
                    <div className="rounded border border-green-900/50 bg-green-950/30 p-2">
                      <div className="text-[10px] uppercase tracking-wide text-green-400 mb-1">Bull case</div>
                      <div className="text-xs text-gray-300">{idea.bull_case}</div>
                    </div>
                    <div className="rounded border border-red-900/50 bg-red-950/30 p-2">
                      <div className="text-[10px] uppercase tracking-wide text-red-400 mb-1">Bear case</div>
                      <div className="text-xs text-gray-300">{idea.bear_case}</div>
                    </div>
                  </div>
                  {issues.length > 0 && (
                    <div className="text-xs text-yellow-400">{issues.join('; ')}</div>
                  )}
                  <div className="text-xs text-gray-500">
                    Estimated: {idea.estimated_qty} shares @ ₹{idea.estimated_price?.toFixed(2)}
                    {' '}(₹{idea.estimated_capital?.toLocaleString('en-IN')})
                  </div>

                  {idea.status === 'debated' && (
                    <button
                      className="btn-primary mt-1"
                      disabled={execute.isPending}
                      onClick={() => execute.mutate(idea.id)}
                    >
                      {execute.isPending ? 'Buying…' : 'Buy Now'}
                    </button>
                  )}
                  {idea.status === 'executed' && (
                    <div className="text-xs text-green-400 mt-1">
                      Bought {idea.executed_qty} @ ₹{idea.executed_price?.toFixed(2)} at {fmtTs(idea.executed_at)}
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
        {!isLoading && ideas.length === 0 && (
          <div className="card text-center text-gray-600 py-8">No ideas submitted yet — try one above.</div>
        )}
      </div>
    </div>
  )
}
