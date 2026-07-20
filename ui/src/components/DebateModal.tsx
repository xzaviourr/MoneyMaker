import { useEffect, useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchJson, type Decision } from '../lib/api'
import { cn } from '../lib/utils'
import { useStore } from '../hooks/useStore'

function agentInfo(id: string): { label: string; color: string; dot: string; room: string } {
  if (id.includes('news_watchdog'))    return { label: 'News Watchdog',       color: 'text-pink-400',   dot: '#f472b6', room: 'Research'   }
  if (id.includes('opportunity'))      return { label: 'Opportunity Scout',   color: 'text-sky-400',    dot: '#38bdf8', room: 'Research'   }
  if (id.includes('bull'))             return { label: 'Bull Advocate',        color: 'text-green-400',  dot: '#4ade80', room: 'Debate'     }
  if (id.includes('bear'))             return { label: 'Bear Advocate',        color: 'text-red-400',    dot: '#f87171', room: 'Debate'     }
  if (id.includes('devil'))            return { label: "Devil's Advocate",     color: 'text-orange-400', dot: '#fb923c', room: 'Debate'     }
  if (id.includes('sector'))           return { label: 'Sector Specialist',    color: 'text-blue-400',   dot: '#60a5fa', room: 'Debate'     }
  if (id.includes('momentum'))         return { label: 'Momentum Analyst',     color: 'text-cyan-400',   dot: '#22d3ee', room: 'Debate'     }
  if (id.includes('committee_chair'))  return { label: 'Committee Chair',      color: 'text-purple-400', dot: '#c084fc', room: 'Verdict'    }
  if (id.includes('alloc'))            return { label: 'Allocation Chair',     color: 'text-indigo-400', dot: '#818cf8', room: 'Sizing'     }
  if (id.includes('risk'))             return { label: 'Risk Gate',            color: 'text-red-400',    dot: '#f87171', room: 'Risk Check' }
  if (id.includes('post_trade'))       return { label: 'Post-Trade Auditor',   color: 'text-gray-400',   dot: '#9ca3af', room: 'Audit'      }
  if (id.includes('execution'))        return { label: 'Execution Trader',     color: 'text-teal-400',   dot: '#2dd4bf', room: 'Execution'  }
  return { label: id.replace(/[._]/g, ' '), color: 'text-gray-400', dot: '#6b7280', room: '—' }
}

function fmtTs(iso: string) {
  const utc = iso.endsWith('Z') ? iso : iso + 'Z'
  return new Date(utc).toLocaleString('en-IN', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}
function fmtDate(iso: string) {
  return new Date(iso).toLocaleString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function safeJson(s: string | null | undefined): Record<string, unknown> {
  try { return JSON.parse(s || '{}') } catch { return {} }
}

const ROOM_COLORS: Record<string, string> = {
  'Research':   'bg-pink-950/60 text-pink-400 border-pink-900/50',
  'Debate':     'bg-gray-800 text-gray-400 border-gray-700',
  'Verdict':    'bg-purple-950/60 text-purple-400 border-purple-900/50',
  'Sizing':     'bg-indigo-950/60 text-indigo-400 border-indigo-900/50',
  'Risk Check': 'bg-red-950/60 text-red-400 border-red-900/50',
  'Audit':      'bg-gray-900 text-gray-500 border-gray-800',
  'Execution':  'bg-teal-950/60 text-teal-400 border-teal-900/50',
}

// ── Pill helper ───────────────────────────────────────────────────────────────
function Pill({ label, color = 'gray' }: { label: string; color?: 'green' | 'red' | 'orange' | 'blue' | 'gray' | 'purple' }) {
  const cls = {
    green:  'bg-green-950/50 text-green-400 border-green-900/40',
    red:    'bg-red-950/50 text-red-400 border-red-900/40',
    orange: 'bg-orange-950/50 text-orange-400 border-orange-900/40',
    blue:   'bg-blue-950/50 text-blue-400 border-blue-900/40',
    purple: 'bg-purple-950/50 text-purple-400 border-purple-900/40',
    gray:   'bg-gray-800 text-gray-400 border-gray-700',
  }[color]
  return <span className={cn('text-[10px] font-semibold px-1.5 py-0.5 rounded-full border', cls)}>{label}</span>
}

// ── Single decision entry card ────────────────────────────────────────────────
function EntryCard({ d }: { d: Decision }) {
  const agent   = agentInfo(d.agent_id)
  const isScout = d.agent_id.includes('opportunity')
  const isBull  = d.agent_id.includes('bull')
  const isBear  = d.agent_id.includes('bear')
  const isDevil = d.agent_id.includes('devil')
  const isSect  = d.agent_id.includes('sector')
  const isMom   = d.agent_id.includes('momentum') && !d.agent_id.includes('pod')
  const isChair = d.agent_id.includes('committee_chair')
  const isRisk  = d.agent_id.includes('risk')
  const isAudit = d.agent_id.includes('post_trade')
  const o = safeJson(d.outputs)
  const inp = safeJson(d.inputs)

  return (
    <div className={cn(
      'rounded-xl border px-4 py-3 space-y-2.5',
      isChair ? 'border-purple-800/50 bg-purple-950/20' :
      isBull  ? 'border-green-900/30 bg-green-950/10'   :
      isBear  ? 'border-red-900/30 bg-red-950/10'       :
      isRisk  ? 'border-red-900/40 bg-red-950/10'       :
      isAudit ? 'border-gray-800 bg-gray-900/40'        :
                'border-gray-800 bg-gray-900/60',
    )}>
      {/* ── Header ── */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="w-2 h-2 rounded-full shrink-0" style={{ background: agent.dot }}/>
        <span className={cn('text-xs font-bold', agent.color)}>{agent.label}</span>
        <span className={cn(
          'text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded-full border',
          ROOM_COLORS[agent.room] || 'bg-gray-800 text-gray-500 border-gray-700',
        )}>
          {agent.room}
        </span>
        {d.decision && (
          <span className="text-[10px] bg-gray-800/80 text-gray-300 px-2 py-0.5 rounded font-mono border border-gray-700/50">
            {d.decision}
          </span>
        )}
        <span className="text-[10px] text-gray-700 font-mono ml-auto">{fmtTs(d.event_ts)}</span>
      </div>

      {/* ── Opportunity Scout ── */}
      {isScout && (
        <>
          {d.reasoning && <p className="text-sm text-gray-200 leading-relaxed font-medium">{d.reasoning}</p>}
          <div className="grid grid-cols-2 gap-2">
            {Array.isArray(o.bull_points) && o.bull_points.length > 0 && (
              <div className="rounded-lg bg-green-950/20 border border-green-900/30 px-3 py-2 space-y-1">
                <div className="text-[9px] text-green-600 font-bold uppercase tracking-wider">Bull Points</div>
                {(o.bull_points as string[]).map((p, i) => (
                  <div key={i} className="text-[11px] text-green-300 flex gap-1.5"><span className="text-green-600 shrink-0">+</span>{p}</div>
                ))}
              </div>
            )}
            {Array.isArray(o.bear_points) && o.bear_points.length > 0 && (
              <div className="rounded-lg bg-red-950/20 border border-red-900/30 px-3 py-2 space-y-1">
                <div className="text-[9px] text-red-600 font-bold uppercase tracking-wider">Bear Points</div>
                {(o.bear_points as string[]).map((p, i) => (
                  <div key={i} className="text-[11px] text-red-300 flex gap-1.5"><span className="text-red-600 shrink-0">−</span>{p}</div>
                ))}
              </div>
            )}
          </div>
          <div className="flex gap-2 flex-wrap">
            {!!o.recommended_position_type && <Pill label={String(o.recommended_position_type)} color="blue" />}
            {typeof o.initial_conviction === 'number' && (
              <Pill label={`${(o.initial_conviction * 100).toFixed(0)}% initial conviction`} color="gray" />
            )}
          </div>
        </>
      )}

      {/* ── Bull Advocate ── */}
      {isBull && (
        <>
          {d.reasoning && <p className="text-sm text-gray-200 leading-relaxed">{d.reasoning}</p>}
          <div className="flex gap-3 flex-wrap">
            {typeof o.price_target_pct_upside === 'number' && (
              <div className="rounded-lg bg-green-950/30 border border-green-900/40 px-3 py-2 text-center min-w-[80px]">
                <div className="text-green-400 font-bold font-mono text-lg">+{(o.price_target_pct_upside as number).toFixed(1)}%</div>
                <div className="text-[9px] text-gray-500">target upside</div>
              </div>
            )}
            {typeof o.time_horizon_weeks === 'number' && (
              <div className="rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-center min-w-[64px]">
                <div className="text-gray-200 font-bold font-mono text-lg">{o.time_horizon_weeks}w</div>
                <div className="text-[9px] text-gray-500">horizon</div>
              </div>
            )}
            {typeof o.conviction_score === 'number' && (
              <div className="rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-center min-w-[64px]">
                <div className="text-green-400 font-bold font-mono text-lg">{(o.conviction_score * 100).toFixed(0)}%</div>
                <div className="text-[9px] text-gray-500">conviction</div>
              </div>
            )}
          </div>
          {Array.isArray(o.key_catalysts) && o.key_catalysts.length > 0 && (
            <div className="space-y-1">
              <div className="text-[9px] text-gray-600 font-bold uppercase tracking-wider">Key Catalysts</div>
              {(o.key_catalysts as string[]).map((c, i) => (
                <div key={i} className="text-[11px] text-gray-300 flex gap-1.5"><span className="text-green-600">▸</span>{c}</div>
              ))}
            </div>
          )}
          {o.technical_support && (
            <div className="text-[11px] text-gray-500 font-mono border-t border-gray-800 pt-1.5">
              Technical: {String(o.technical_support)}
            </div>
          )}
        </>
      )}

      {/* ── Bear Advocate ── */}
      {isBear && (
        <>
          {d.reasoning && <p className="text-sm text-gray-200 leading-relaxed">{d.reasoning}</p>}
          <div className="flex gap-3 flex-wrap">
            {typeof o.max_downside_pct === 'number' && (
              <div className="rounded-lg bg-red-950/30 border border-red-900/40 px-3 py-2 text-center min-w-[80px]">
                <div className="text-red-400 font-bold font-mono text-lg">-{(o.max_downside_pct as number).toFixed(1)}%</div>
                <div className="text-[9px] text-gray-500">max downside</div>
              </div>
            )}
            {typeof o.conviction_score === 'number' && (
              <div className="rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-center min-w-[64px]">
                <div className="text-red-400 font-bold font-mono text-lg">{(o.conviction_score * 100).toFixed(0)}%</div>
                <div className="text-[9px] text-gray-500">bear conviction</div>
              </div>
            )}
          </div>
          {Array.isArray(o.key_risks) && o.key_risks.length > 0 && (
            <div className="space-y-1">
              <div className="text-[9px] text-gray-600 font-bold uppercase tracking-wider">Key Risks</div>
              {(o.key_risks as string[]).map((r, i) => (
                <div key={i} className="text-[11px] text-red-300 flex gap-1.5"><span className="text-red-600">▸</span>{r}</div>
              ))}
            </div>
          )}
          {o.invalidation_scenario && (
            <div className="rounded-lg bg-gray-800/60 border border-gray-700/40 px-3 py-1.5">
              <span className="text-[9px] text-gray-600 font-bold uppercase tracking-wider">Bull wins if: </span>
              <span className="text-[11px] text-gray-400">{String(o.invalidation_scenario)}</span>
            </div>
          )}
        </>
      )}

      {/* ── Devil's Advocate ── */}
      {isDevil && (
        <>
          <div className="flex gap-2 items-center flex-wrap">
            {!!o.go_no_go_lean && (
              <Pill
                label={String(o.go_no_go_lean).replace('_', ' ')}
                color={o.go_no_go_lean === 'go' ? 'green' : o.go_no_go_lean === 'no_go' ? 'red' : 'orange'}
              />
            )}
            {typeof o.stress_test_score === 'number' && (
              <Pill label={`stress ${(o.stress_test_score * 100).toFixed(0)}%`} color="gray" />
            )}
          </div>
          {d.reasoning && <p className="text-[12px] text-gray-400 leading-relaxed italic">{d.reasoning}</p>}
          {Array.isArray(o.tail_risks) && o.tail_risks.length > 0 && (
            <div className="space-y-1">
              <div className="text-[9px] text-orange-700 font-bold uppercase tracking-wider">Tail Risks</div>
              {(o.tail_risks as string[]).map((r, i) => (
                <div key={i} className="text-[11px] text-orange-300/80 flex gap-1.5"><span className="text-orange-600">⚠</span>{r}</div>
              ))}
            </div>
          )}
          {o.liquidity_concerns && (
            <div className="text-[11px] text-gray-500 border-t border-gray-800 pt-1.5">
              Liquidity: {String(o.liquidity_concerns)}
            </div>
          )}
        </>
      )}

      {/* ── Sector Specialist ── */}
      {isSect && (
        <>
          <div className="flex gap-2 items-center flex-wrap">
            {!!o.sector && <Pill label={String(o.sector)} color="blue" />}
            {!!o.specialist_verdict && (
              <Pill
                label={String(o.specialist_verdict)}
                color={o.specialist_verdict === 'support' ? 'green' : o.specialist_verdict === 'oppose' ? 'red' : 'gray'}
              />
            )}
            {!!o.sector_rotation_signal && <Pill label={String(o.sector_rotation_signal)} color="gray" />}
            {typeof o.sector_conviction_modifier === 'number' && (
              <Pill
                label={`modifier ${(o.sector_conviction_modifier as number) >= 0 ? '+' : ''}${(o.sector_conviction_modifier as number).toFixed(2)}`}
                color={(o.sector_conviction_modifier as number) >= 0 ? 'green' : 'red'}
              />
            )}
          </div>
          {d.reasoning && <p className="text-[12px] text-gray-400 leading-relaxed">{d.reasoning}</p>}
        </>
      )}

      {/* ── Momentum Analyst ── */}
      {isMom && (
        <>
          <div className="flex gap-2 items-center flex-wrap">
            {!!o.trend_quality && (
              <Pill
                label={String(o.trend_quality)}
                color={String(o.trend_quality).includes('strong') ? 'green' : String(o.trend_quality).includes('weak') ? 'red' : 'gray'}
              />
            )}
            {!!o.momentum_phase && <Pill label={String(o.momentum_phase)} color="blue" />}
            {typeof o.technical_score === 'number' && (
              <Pill label={`score ${(o.technical_score * 100).toFixed(0)}%`} color="gray" />
            )}
            {typeof o.momentum_conviction_modifier === 'number' && (
              <Pill
                label={`modifier ${(o.momentum_conviction_modifier as number) >= 0 ? '+' : ''}${(o.momentum_conviction_modifier as number).toFixed(2)}`}
                color={(o.momentum_conviction_modifier as number) >= 0 ? 'green' : 'red'}
              />
            )}
          </div>
          {d.reasoning && <p className="text-[12px] text-gray-400 leading-relaxed">{d.reasoning}</p>}
        </>
      )}

      {/* ── Committee Chair ── */}
      {isChair && (
        <>
          <div className="grid grid-cols-3 gap-2">
            {typeof inp.bull_conviction === 'number' && (
              <div className="rounded-lg bg-green-950/40 border border-green-900/40 px-2 py-2 text-center">
                <div className="text-green-400 font-bold font-mono text-base">{(inp.bull_conviction * 100).toFixed(0)}%</div>
                <div className="text-[10px] text-gray-500 mt-0.5">Bull conviction</div>
              </div>
            )}
            {typeof inp.bear_conviction === 'number' && (
              <div className="rounded-lg bg-red-950/40 border border-red-900/40 px-2 py-2 text-center">
                <div className="text-red-400 font-bold font-mono text-base">{(inp.bear_conviction * 100).toFixed(0)}%</div>
                <div className="text-[10px] text-gray-500 mt-0.5">Bear conviction</div>
              </div>
            )}
            {typeof o.final_conviction === 'number' && (
              <div className={cn('rounded-lg border px-2 py-2 text-center', o.approved ? 'bg-purple-950/40 border-purple-900/40' : 'bg-gray-900 border-gray-800')}>
                <div className={cn('font-bold font-mono text-base', o.approved ? 'text-purple-400' : 'text-gray-500')}>
                  {(o.final_conviction * 100).toFixed(0)}%
                </div>
                <div className={cn('text-[10px] mt-0.5', o.approved ? 'text-green-500' : 'text-red-500')}>
                  {o.approved ? '✓ Approved' : '✗ Rejected'}
                </div>
              </div>
            )}
          </div>
          {o.position_tier && (
            <div className="flex gap-2">
              <Pill label={`Position: ${String(o.position_tier)}`} color={o.approved ? 'purple' : 'gray'} />
            </div>
          )}
          {d.reasoning && <p className="text-sm text-gray-200 leading-relaxed">{d.reasoning}</p>}
        </>
      )}

      {/* ── Risk Gate ── */}
      {isRisk && o.passed != null && (
        <div className={cn(
          'rounded-lg px-3 py-2 text-sm font-semibold border',
          o.passed ? 'bg-green-950/40 border-green-900/40 text-green-400' : 'bg-red-950/40 border-red-900/40 text-red-400',
        )}>
          {o.passed ? '✓ Risk gate PASSED' : '✗ Risk gate BLOCKED'}
          {Array.isArray(o.reasons) && o.reasons.length > 0 && (
            <span className="text-gray-400 font-normal ml-2 text-xs">— {(o.reasons as string[]).join(', ')}</span>
          )}
        </div>
      )}

      {/* ── Reasoning fallback (agents without special display) ── */}
      {!isScout && !isBull && !isBear && !isDevil && !isSect && !isMom && !isChair && d.reasoning && (
        <p className="text-sm text-gray-300 leading-relaxed whitespace-pre-line">{d.reasoning}</p>
      )}

      {/* ── Outcome ── */}
      {d.outcome && (
        <div className="rounded-lg bg-gray-800/60 border border-gray-700/40 px-3 py-2">
          <div className="text-[9px] uppercase tracking-widest text-gray-600 font-semibold mb-1">Outcome</div>
          <p className="text-xs text-gray-200 leading-relaxed">{d.outcome}</p>
        </div>
      )}
    </div>
  )
}

// ── Debate summary — the confrontation view ────────────────────────────────────
function DebateSummaryCard({ decisions }: { decisions: Decision[] }) {
  const scout  = decisions.find(d => d.agent_id.includes('opportunity'))
  const bull   = decisions.find(d => d.agent_id.includes('bull'))
  const bear   = decisions.find(d => d.agent_id.includes('bear'))
  const devil  = decisions.find(d => d.agent_id.includes('devil'))
  const sector = decisions.find(d => d.agent_id.includes('sector'))
  const mom    = decisions.find(d => d.agent_id.includes('momentum') && !d.agent_id.includes('pod'))
  const chair  = decisions.find(d => d.agent_id.includes('committee_chair'))

  const bullO   = safeJson(bull?.outputs)
  const bearO   = safeJson(bear?.outputs)
  const devilO  = safeJson(devil?.outputs)
  const sectorO = safeJson(sector?.outputs)
  const momO    = safeJson(mom?.outputs)
  const chairO  = safeJson(chair?.outputs)

  const approved = typeof chairO.approved === 'boolean' ? chairO.approved : null

  return (
    <div className="rounded-xl border border-gray-700/50 bg-gray-900/40 overflow-hidden">

      {/* Thesis */}
      {scout?.reasoning && (
        <div className="px-4 py-3 border-b border-gray-800/60">
          <div className="text-[9px] text-sky-600 font-bold uppercase tracking-wider mb-1">Scout's Thesis</div>
          <p className="text-sm text-gray-200 leading-relaxed">{scout.reasoning}</p>
        </div>
      )}

      {/* Bull vs Bear — side-by-side confrontation */}
      {(bull || bear) && (
        <div className="grid grid-cols-2 divide-x divide-gray-800/60">
          <div className="p-4 space-y-2 bg-green-950/10">
            <div className="text-[9px] text-green-600 font-bold uppercase tracking-wider">Bull Case</div>
            {typeof bullO.price_target_pct_upside === 'number' && (
              <div className="text-green-400 font-bold font-mono text-2xl leading-none">
                +{(bullO.price_target_pct_upside as number).toFixed(1)}%
                {typeof bullO.time_horizon_weeks === 'number' && (
                  <span className="text-sm text-gray-500 font-normal ml-1">in {bullO.time_horizon_weeks}w</span>
                )}
              </div>
            )}
            {bull?.reasoning && (
              <p className="text-[11px] text-gray-300 leading-relaxed">{bull.reasoning}</p>
            )}
            {Array.isArray(bullO.key_catalysts) && (bullO.key_catalysts as string[]).slice(0, 2).map((c, i) => (
              <div key={i} className="text-[10px] text-green-300/70 flex gap-1.5 leading-snug">
                <span className="text-green-700 shrink-0">▸</span>{c}
              </div>
            ))}
            {!!bullO.technical_support && (
              <div className="text-[10px] text-gray-600 font-mono pt-0.5">Support: {String(bullO.technical_support)}</div>
            )}
          </div>
          <div className="p-4 space-y-2 bg-red-950/10">
            <div className="text-[9px] text-red-600 font-bold uppercase tracking-wider">Bear Case</div>
            {typeof bearO.max_downside_pct === 'number' && (
              <div className="text-red-400 font-bold font-mono text-2xl leading-none">
                -{(bearO.max_downside_pct as number).toFixed(1)}%
              </div>
            )}
            {bear?.reasoning && (
              <p className="text-[11px] text-gray-300 leading-relaxed">{bear.reasoning}</p>
            )}
            {Array.isArray(bearO.key_risks) && (bearO.key_risks as string[]).slice(0, 2).map((r, i) => (
              <div key={i} className="text-[10px] text-red-300/70 flex gap-1.5 leading-snug">
                <span className="text-red-700 shrink-0">▸</span>{r}
              </div>
            ))}
            {!!bearO.invalidation_scenario && (
              <div className="text-[10px] text-gray-600 font-mono pt-0.5">Bull wins if: {String(bearO.invalidation_scenario)}</div>
            )}
          </div>
        </div>
      )}

      {/* Devil's critique */}
      {devil && (
        <div className="px-4 py-2.5 border-t border-gray-800/60 bg-orange-950/5 space-y-1.5">
          <div className="flex items-center gap-2 flex-wrap">
            <div className="text-[9px] text-orange-600 font-bold uppercase tracking-wider">Devil's Critique</div>
            {!!devilO.go_no_go_lean && (
              <Pill
                label={String(devilO.go_no_go_lean).replace('_', ' ')}
                color={devilO.go_no_go_lean === 'go' ? 'green' : devilO.go_no_go_lean === 'no_go' ? 'red' : 'orange'}
              />
            )}
            {typeof devilO.stress_test_score === 'number' && (
              <Pill label={`stress ${(devilO.stress_test_score as number * 100).toFixed(0)}%`} color="gray" />
            )}
          </div>
          {!!devilO.bull_flaw && (
            <p className="text-[11px] text-gray-400 leading-snug">
              <span className="text-green-700 font-semibold">Bull flaw: </span>{String(devilO.bull_flaw)}
            </p>
          )}
          {!!devilO.bear_flaw && (
            <p className="text-[11px] text-gray-400 leading-snug">
              <span className="text-red-700 font-semibold">Bear flaw: </span>{String(devilO.bear_flaw)}
            </p>
          )}
          {Array.isArray(devilO.tail_risks) && (devilO.tail_risks as string[]).length > 0 && (
            <div className="text-[10px] text-orange-400/60">
              ⚠ {(devilO.tail_risks as string[]).join(' · ')}
            </div>
          )}
        </div>
      )}

      {/* Sector + Momentum signals */}
      {(sector || mom) && (
        <div className="px-4 py-2 border-t border-gray-800/60 flex items-center gap-4 flex-wrap text-[10px] text-gray-500">
          {!!sectorO.sector && (
            <span>
              Sector: <span className="text-blue-400">{String(sectorO.sector)}</span>
              {sectorO.specialist_verdict ? <span> · <span className={sectorO.specialist_verdict === 'support' ? 'text-green-400' : sectorO.specialist_verdict === 'oppose' ? 'text-red-400' : 'text-gray-400'}>{String(sectorO.specialist_verdict)}</span></span> : null}
              {sectorO.sector_rotation_signal ? <span> · {String(sectorO.sector_rotation_signal)}</span> : null}
            </span>
          )}
          {!!momO.trend_quality && (
            <span>
              Trend: <span className="text-cyan-400">{String(momO.trend_quality)}</span>
              {momO.momentum_phase ? <span> · {String(momO.momentum_phase)}</span> : null}
              {typeof momO.technical_score === 'number' ? <span> · score {((momO.technical_score as number) * 100).toFixed(0)}%</span> : null}
            </span>
          )}
        </div>
      )}

      {/* Chair verdict */}
      {chair && (
        <div className={cn(
          'px-4 py-3 border-t',
          approved === true  ? 'border-green-900/40 bg-green-950/15' :
          approved === false ? 'border-red-900/30 bg-red-950/10' :
                               'border-gray-800 bg-gray-900/30',
        )}>
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className={cn('text-xs font-bold', approved ? 'text-green-400' : approved === false ? 'text-red-400' : 'text-gray-500')}>
              {approved ? '✓ APPROVED' : approved === false ? '✗ REJECTED' : 'PENDING VERDICT'}
            </span>
            {typeof chairO.final_conviction === 'number' && (
              <span className="text-[10px] text-gray-500 font-mono">
                {((chairO.final_conviction as number) * 100).toFixed(0)}% final conviction
              </span>
            )}
            {!!chairO.position_tier && (
              <Pill label={`Position: ${String(chairO.position_tier)}`} color={approved ? 'purple' : 'gray'} />
            )}
          </div>
          {chair.reasoning && <p className="text-xs text-gray-300 leading-relaxed">{chair.reasoning}</p>}
          {chair.outcome && (
            <div className="mt-1.5 text-[10px] text-gray-400 font-mono border-t border-gray-800/60 pt-1.5">
              {chair.outcome}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Session block ─────────────────────────────────────────────────────────────
type DebateSession = { ts: string; approved: boolean | null; decisions: Decision[] }

function SessionBlock({ session, index, defaultOpen }: {
  session: DebateSession
  index:   number
  defaultOpen: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  const [showRaw, setShowRaw] = useState(false)
  const chair = session.decisions.find(d => d.agent_id.includes('committee_chair'))
  const outputs = safeJson(chair?.outputs)
  const approved = typeof outputs.approved === 'boolean' ? outputs.approved : null

  return (
    <div className="space-y-2">
      {/* session header */}
      <button
        className={cn(
          'w-full text-left flex items-center gap-3 px-4 py-3 rounded-xl border transition-colors',
          approved === true  ? 'border-green-900/40 bg-green-950/15 hover:bg-green-950/25' :
          approved === false ? 'border-red-900/40 bg-red-950/10 hover:bg-red-950/20'       :
                               'border-gray-800 bg-gray-900/60 hover:bg-gray-900',
        )}
        onClick={() => setOpen(v => !v)}
      >
        <div className="shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold"
          style={{ background: approved === true ? '#14532d66' : approved === false ? '#7f1d1d66' : '#1f2937' }}>
          {session.decisions.length}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-xs font-semibold text-white">
            {index === 0 ? 'Latest Debate — ' : `Earlier Debate — `}
            {fmtDate(session.ts)}
          </div>
          <div className="text-[10px] text-gray-500 font-mono">
            {session.decisions.length} agent entries
            {approved !== null && (
              <span className={cn('ml-2 font-semibold', approved ? 'text-green-500' : 'text-red-400')}>
                · {approved ? '✓ Approved' : '✗ Rejected'}
              </span>
            )}
          </div>
        </div>
        <span className="text-gray-600 text-xs shrink-0">{open ? '▲' : '▼'}</span>
      </button>

      {/* session content */}
      {open && (
        <div className="space-y-2 pl-3 border-l border-gray-800/60">
          <DebateSummaryCard decisions={session.decisions} />

          <button
            onClick={() => setShowRaw(v => !v)}
            className="text-[10px] text-gray-600 hover:text-gray-400 transition-colors flex items-center gap-1 py-0.5"
          >
            <span>{showRaw ? '▲' : '▼'}</span>
            {showRaw ? 'Hide' : 'Show'} individual agent entries ({session.decisions.length})
          </button>

          {showRaw && session.decisions.map((d, i) => <EntryCard key={i} d={d} />)}
        </div>
      )}
    </div>
  )
}

// ── Main panel ────────────────────────────────────────────────────────────────
export function DebateModal({ symbol, onClose }: { symbol: string; onClose: () => void }) {
  const selectedPortfolioId = useStore(s => s.selectedPortfolioId)
  const { data: newest = [], isLoading } = useQuery<Decision[]>({
    queryKey: ['debate', symbol, selectedPortfolioId],
    queryFn:  () => fetchJson(`/decisions/?symbol=${encodeURIComponent(symbol)}&limit=200`),
    staleTime: 0,
  })

  // Chronological order
  const decisions = useMemo(() => [...newest].reverse(), [newest])

  // Group into sessions: each committee_chair entry marks the end of a session.
  // Any trailing entries after the last chair (in-progress debate) form the last session.
  const sessions = useMemo<DebateSession[]>(() => {
    const result: DebateSession[] = []
    let current: Decision[] = []

    for (const d of decisions) {
      current.push(d)
      if (d.agent_id.includes('committee_chair')) {
        const outputs = safeJson(d.outputs)
        result.push({
          ts:        d.event_ts,
          approved:  typeof outputs.approved === 'boolean' ? outputs.approved : null,
          decisions: [...current],
        })
        current = []
      }
    }
    // in-progress debate (no chair verdict yet)
    if (current.length > 0) {
      const last = current[current.length - 1]
      result.push({ ts: last.event_ts, approved: null, decisions: current })
    }
    // Most recent first
    return result.reverse()
  }, [decisions])

  const roomGroups = useMemo(() => {
    const acc: Record<string, Decision[]> = {}
    for (const d of decisions) {
      const room = agentInfo(d.agent_id).room
      if (!acc[room]) acc[room] = []
      acc[room].push(d)
    }
    return acc
  }, [decisions])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <>
      <style>{`
        @keyframes slideInRight {
          from { transform: translateX(100%); opacity: 0; }
          to   { transform: translateX(0);   opacity: 1; }
        }
        .debate-panel { animation: slideInRight 0.22s cubic-bezier(0.16,1,0.3,1); }
      `}</style>

      {/* dim backdrop */}
      <div className="fixed inset-0 z-40 bg-black/50" onClick={onClose} />

      {/* side panel */}
      <div className="debate-panel fixed right-0 top-0 z-50 h-screen w-full max-w-[580px] bg-gray-950 border-l border-gray-800 flex flex-col shadow-2xl">

        {/* header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800 shrink-0 bg-gray-950">
          <div className="space-y-0.5">
            <div className="flex items-center gap-2">
              <span className="font-mono font-bold text-2xl text-white tracking-tight">{symbol}</span>
              <span className="text-xs text-gray-500 border border-gray-800 px-2 py-0.5 rounded-full">
                Debate History
              </span>
            </div>
            <p className="text-[11px] text-gray-600 font-mono">
              {sessions.length} debate session{sessions.length !== 1 ? 's' : ''} · {decisions.length} total entries · Esc to close
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-white w-9 h-9 flex items-center justify-center rounded-lg hover:bg-gray-800 transition-colors text-lg"
            aria-label="Close debate panel"
          >
            ✕
          </button>
        </div>

        {/* pipeline progress — based on latest session */}
        {sessions.length > 0 && (() => {
          const latestRooms = sessions[0].decisions.reduce<Record<string, boolean>>((acc, d) => {
            acc[agentInfo(d.agent_id).room] = true
            return acc
          }, {})
          return (
            <div className="px-5 py-2.5 border-b border-gray-800/60 shrink-0">
              <div className="flex items-center gap-1 flex-wrap text-[10px] font-semibold">
                {['Research', 'Debate', 'Verdict', 'Sizing', 'Risk Check', 'Execution', 'Audit'].map((room, i, arr) => {
                  const has = !!latestRooms[room]
                  return (
                    <div key={room} className="flex items-center gap-1">
                      <span className={cn(
                        'px-1.5 py-0.5 rounded-full border',
                        has ? ROOM_COLORS[room] : 'bg-gray-900 text-gray-700 border-gray-800',
                      )}>
                        {room}
                      </span>
                      {i < arr.length - 1 && (
                        <span className={cn('text-xs', has ? 'text-gray-500' : 'text-gray-800')}>→</span>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })()}

        {/* scrollable body */}
        <div className="overflow-y-auto flex-1 px-5 py-4 space-y-3">
          {isLoading && (
            <div className="flex items-center justify-center py-16 text-gray-600 text-sm">
              Loading debate…
            </div>
          )}

          {!isLoading && sessions.length === 0 && (
            <div className="flex flex-col items-center justify-center py-16 space-y-2 text-center">
              <div className="text-4xl">📭</div>
              <div className="text-gray-500 text-sm">No debate records for {symbol}</div>
              <div className="text-gray-700 text-xs">Debates are recorded when the Long-term Desk analyses this stock</div>
            </div>
          )}

          {sessions.map((session, i) => (
            <SessionBlock key={i} session={session} index={i} defaultOpen={i === 0} />
          ))}
        </div>

        {/* footer */}
        <div className="px-5 py-3 border-t border-gray-800 shrink-0 flex items-center justify-between text-xs text-gray-600">
          <span>
            {sessions.length} session{sessions.length !== 1 ? 's' : ''} · {decisions.length} entries · {Object.keys(roomGroups).length} stages
          </span>
          <button onClick={onClose} className="text-gray-500 hover:text-white transition-colors px-3 py-1.5 rounded-lg hover:bg-gray-800">
            Close panel
          </button>
        </div>
      </div>
    </>
  )
}
