import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchJson, type Decision } from '../lib/api'
import { cn } from '../lib/utils'

function agentInfo(id: string): { label: string; color: string; room: string } {
  if (id.includes('news_watchdog'))   return { label: 'News Watchdog',     color: 'text-pink-400',   room: 'Room 1 · Research'   }
  if (id.includes('bull'))            return { label: 'Bull Advocate',      color: 'text-green-400',  room: 'Room 1 · Debate'     }
  if (id.includes('bear'))            return { label: 'Bear Advocate',      color: 'text-red-400',    room: 'Room 1 · Debate'     }
  if (id.includes('devil'))           return { label: "Devil's Advocate",   color: 'text-orange-400', room: 'Room 1 · Debate'     }
  if (id.includes('sector'))          return { label: 'Sector Specialist',  color: 'text-blue-400',   room: 'Room 1 · Debate'     }
  if (id.includes('momentum'))        return { label: 'Momentum Analyst',   color: 'text-cyan-400',   room: 'Room 1 · Debate'     }
  if (id.includes('committee_chair')) return { label: 'Committee Chair',    color: 'text-purple-400', room: 'Room 1 · Verdict'    }
  if (id.includes('alloc'))           return { label: 'Allocation Chair',   color: 'text-indigo-400', room: 'Room 2 · Sizing'     }
  if (id.includes('risk'))            return { label: 'Risk Gate',          color: 'text-red-400',    room: 'Room 2 · Risk Check' }
  if (id.includes('post_trade'))      return { label: 'Post-Trade Auditor', color: 'text-gray-400',   room: 'Room 3 · Audit'      }
  if (id.includes('execution'))       return { label: 'Execution Trader',   color: 'text-teal-400',   room: 'Room 3 · Execution'  }
  return { label: id.replace(/[._]/g, ' '), color: 'text-gray-400', room: '—' }
}

function fmtTs(iso: string) {
  return new Date(iso).toLocaleString('en-IN', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function safeJson(s: string | null | undefined): Record<string, unknown> {
  try { return JSON.parse(s || '{}') } catch { return {} }
}

export function DebateModal({ symbol, onClose }: { symbol: string; onClose: () => void }) {
  const { data: newest = [], isLoading } = useQuery<Decision[]>({
    queryKey: ['debate', symbol],
    queryFn:  () => fetchJson(`/decisions/?symbol=${encodeURIComponent(symbol)}&limit=100`),
    staleTime: 0,
  })

  // API returns newest first — reverse to show chronological debate flow
  const decisions = [...newest].reverse()

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-gray-950 border border-gray-800 rounded-2xl w-full max-w-3xl max-h-[90vh] flex flex-col shadow-2xl">

        {/* header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800 shrink-0">
          <div className="space-y-0.5">
            <div className="flex items-center gap-3">
              <span className="font-mono font-bold text-xl text-white">{symbol}</span>
              <span className="text-sm text-gray-500">— Full AI Debate History</span>
            </div>
            <p className="text-xs text-gray-600">
              Oldest entry first · {decisions.length} agent entries
            </p>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white text-xl w-8 h-8 flex items-center justify-center rounded-lg hover:bg-gray-800 transition-colors">
            ✕
          </button>
        </div>

        {/* body */}
        <div className="overflow-y-auto flex-1 px-6 py-5 space-y-3">
          {isLoading && (
            <div className="text-gray-500 text-sm py-8 text-center">Loading debate…</div>
          )}
          {!isLoading && decisions.length === 0 && (
            <div className="text-gray-600 text-sm py-8 text-center">
              No debate records found for {symbol} in the ledger.
            </div>
          )}

          {decisions.map((d, i) => {
            const agent   = agentInfo(d.agent_id)
            const isChair = d.agent_id.includes('committee_chair')
            const isRisk  = d.agent_id.includes('risk')
            const outputs = safeJson(d.outputs)
            const inputs  = safeJson(d.inputs)

            return (
              <div key={i} className={cn(
                'rounded-xl border px-4 py-3 space-y-2',
                isChair ? 'border-purple-900/60 bg-purple-950/15' :
                isRisk  ? 'border-red-900/40 bg-red-950/10'       :
                          'border-gray-800 bg-gray-900',
              )}>
                {/* agent header */}
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={cn('text-xs font-bold', agent.color)}>{agent.label}</span>
                  <span className="text-[10px] text-gray-600 border border-gray-800 px-1.5 py-0.5 rounded-full">
                    {agent.room}
                  </span>
                  {d.decision && (
                    <span className="text-[10px] text-gray-500 bg-gray-800 px-1.5 py-0.5 rounded">
                      {d.decision}
                    </span>
                  )}
                  <span className="text-[10px] text-gray-700 font-mono ml-auto">{fmtTs(d.event_ts)}</span>
                </div>

                {/* conviction scores — committee chair */}
                {isChair && (
                  <div className="flex gap-4 text-xs">
                    {typeof inputs.bull_conviction === 'number' && (
                      <span className="text-green-400">
                        Bull conviction: {(inputs.bull_conviction * 100).toFixed(0)}%
                      </span>
                    )}
                    {typeof inputs.bear_conviction === 'number' && (
                      <span className="text-red-400">
                        Bear conviction: {(inputs.bear_conviction * 100).toFixed(0)}%
                      </span>
                    )}
                    {typeof outputs.final_conviction === 'number' && (
                      <span className="text-purple-400 font-semibold">
                        Final: {(outputs.final_conviction * 100).toFixed(0)}%
                        {outputs.approved === true  ? ' · Approved ✓' : ''}
                        {outputs.approved === false ? ' · Rejected ✗' : ''}
                      </span>
                    )}
                    {outputs.position_tier != null && (
                      <span className="text-gray-500">tier: {String(outputs.position_tier)}</span>
                    )}
                  </div>
                )}

                {/* risk gate output */}
                {isRisk && outputs.passed != null && (
                  <div className={cn(
                    'text-xs font-semibold',
                    outputs.passed ? 'text-green-400' : 'text-red-400',
                  )}>
                    {outputs.passed ? 'Risk gate PASSED' : 'Risk gate BLOCKED'}
                    {Array.isArray(outputs.reasons) && outputs.reasons.length > 0 && (
                      <span className="text-gray-400 font-normal ml-1">
                        — {(outputs.reasons as string[]).join(', ')}
                      </span>
                    )}
                  </div>
                )}

                {/* full reasoning */}
                {d.reasoning && (
                  <p className="text-sm text-gray-300 leading-relaxed whitespace-pre-line">{d.reasoning}</p>
                )}

                {/* outcome */}
                {d.outcome && (
                  <div className="rounded bg-gray-800/60 px-3 py-2 text-xs">
                    <span className="text-gray-500 mr-2">Outcome:</span>
                    <span className="text-gray-200">{d.outcome}</span>
                  </div>
                )}
              </div>
            )
          })}
        </div>

        <div className="px-6 py-3 border-t border-gray-800 shrink-0 flex justify-end">
          <button onClick={onClose} className="btn-ghost text-sm">Close</button>
        </div>
      </div>
    </div>
  )
}
