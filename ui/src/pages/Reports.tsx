import { useState, useMemo, useRef, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchJson, type Trade, type CapitalSnapshot } from '../lib/api'
import { fmtInr, fmtPrice, cn } from '../lib/utils'

// ── Theme ─────────────────────────────────────────────────────────────────────
function useDarkMode() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))
  useEffect(() => {
    const obs = new MutationObserver(() => setDark(document.documentElement.classList.contains('dark')))
    obs.observe(document.documentElement, { attributeFilter: ['class'] })
    return () => obs.disconnect()
  }, [])
  return dark
}

// ── Stats ─────────────────────────────────────────────────────────────────────
function arrMean(a: number[]) { return a.length ? a.reduce((s, v) => s + v, 0) / a.length : 0 }
function arrStd(a: number[], m = arrMean(a)) {
  return a.length < 2 ? 0 : Math.sqrt(a.reduce((s, v) => s + (v - m) ** 2, 0) / a.length)
}
function normalCdf(z: number) {
  const p = 0.3275911
  const [a1, a2, a3, a4, a5] = [0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429]
  const sign = z < 0 ? -1 : 1
  const x = Math.abs(z) / Math.SQRT2
  const t = 1 / (1 + p * x)
  const y = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-x * x)
  return 0.5 * (1 + sign * y)
}
function annualSharpe(daily: number[]): number | null {
  if (daily.length < 2) return null
  const m = arrMean(daily); const s = arrStd(daily, m)
  return s === 0 ? null : (m / s) * Math.sqrt(252)
}
function annualSortino(daily: number[]): number | null {
  if (daily.length < 2) return null
  const m = arrMean(daily)
  const neg = daily.filter(v => v < 0)
  if (!neg.length) return m > 0 ? 99 : null
  const ds = Math.sqrt(neg.reduce((s, v) => s + v * v, 0) / neg.length)
  return ds === 0 ? null : (m / ds) * Math.sqrt(252)
}
function tStatPVal(pnls: number[]) {
  if (pnls.length < 2) return { t: null as number | null, p: null as number | null }
  const m = arrMean(pnls); const s = arrStd(pnls, m)
  if (s === 0) return { t: null, p: null }
  const t = m / (s / Math.sqrt(pnls.length))
  return { t, p: 2 * (1 - normalCdf(Math.abs(t))) }
}
function calcCAGR(pnl: number, cap: number, firstTs: string, lastTs: string): number | null {
  if (cap <= 0) return null
  const days = (new Date(lastTs).getTime() - new Date(firstTs).getTime()) / 86400000
  if (days < 1) return null
  return Math.pow(1 + pnl / cap, 365 / days) - 1
}
function medianOf(arr: number[]): number {
  if (!arr.length) return 0
  const s = [...arr].sort((a, b) => a - b)
  return s.length % 2 === 0 ? (s[s.length / 2 - 1] + s[s.length / 2]) / 2 : s[Math.floor(s.length / 2)]
}

// ── Period ────────────────────────────────────────────────────────────────────
type Period = 'today' | '7d' | 'mtd' | '30d' | '90d' | 'ytd' | 'all'
const PERIODS: { key: Period; label: string }[] = [
  { key: 'today', label: 'Today' }, { key: '7d', label: '7D' }, { key: 'mtd', label: 'MTD' },
  { key: '30d', label: '30D' }, { key: '90d', label: '90D' }, { key: 'ytd', label: 'YTD' },
  { key: 'all', label: 'All Time' },
]
function periodStart(p: Period): Date | null {
  const now = new Date()
  if (p === 'today') return new Date(now.getFullYear(), now.getMonth(), now.getDate())
  if (p === '7d') { const d = new Date(now); d.setDate(d.getDate() - 6); d.setHours(0, 0, 0, 0); return d }
  if (p === 'mtd') return new Date(now.getFullYear(), now.getMonth(), 1)
  if (p === '30d') { const d = new Date(now); d.setDate(d.getDate() - 29); d.setHours(0, 0, 0, 0); return d }
  if (p === '90d') { const d = new Date(now); d.setDate(d.getDate() - 89); d.setHours(0, 0, 0, 0); return d }
  if (p === 'ytd') return new Date(now.getFullYear(), 0, 1)
  return null
}
function inPeriod(t: Trade, s: Date | null) { return !s || new Date(t.timestamp) >= s }

// ── Format helpers ────────────────────────────────────────────────────────────
function fmtCr(v: number) {
  const abs = Math.abs(v); const sign = v < 0 ? '-' : ''
  if (abs >= 1_00_00_000) return `${sign}₹${(abs / 1_00_00_000).toFixed(1)}Cr`
  if (abs >= 1_00_000)    return `${sign}₹${(abs / 1_00_000).toFixed(1)}L`
  if (abs >= 1_000)       return `${sign}₹${(abs / 1_000).toFixed(1)}K`
  return fmtInr(v)
}
function fmtAxY(v: number): string {
  const abs = Math.abs(v); const sign = v < 0 ? '-' : ''
  if (abs >= 1_00_000) return `${sign}₹${(abs / 1_00_000).toFixed(1)}L`
  if (abs >= 1_000)    return `${sign}₹${(abs / 1_000).toFixed(0)}K`
  return `${sign}₹${Math.round(abs)}`
}
function fmtDay(iso: string) { return new Date(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' }) }
function fmtDateTime(iso: string) {
  return new Date(iso).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}
function fmtStat(v: number | null, dp = 2): string {
  if (v === null || !Number.isFinite(v)) return '—'
  return v.toFixed(dp)
}
function holdDuration(entryTs: string | null, exitTs: string): string {
  if (!entryTs) return '—'
  const ms = new Date(exitTs).getTime() - new Date(entryTs).getTime()
  if (!Number.isFinite(ms) || ms <= 0) return '—'
  const m = Math.round(ms / 60000)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60); if (h < 24) return `${h}h ${m % 60}m`
  return `${Math.floor(h / 24)}d ${h % 24}h`
}

// ── Equity Curve + Drawdown ───────────────────────────────────────────────────
const EH = 188, DH = 52, Y_PAD = 8

function EquityDrawdown({ equity, tsArr, isUp, isDark }: {
  equity: number[]; tsArr: string[]; isUp: boolean; isDark: boolean
}) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)
  const chartRef = useRef<HTMLDivElement>(null)
  if (equity.length < 2) return (
    <div className="h-56 flex items-center justify-center text-gray-700 text-xs">
      Complete 2+ trades to see the equity curve
    </div>
  )
  const eColor = isUp ? '#22c55e' : '#ef4444'
  const W = 1000
  const eMin = Math.min(0, ...equity), eMax = Math.max(0, ...equity)
  const eRange = eMax - eMin || 1
  function yPct(v: number) { return ((Y_PAD + (1 - (v - eMin) / eRange) * (EH - 2 * Y_PAD)) / EH) * 100 }
  const esx = (i: number) => (i / (equity.length - 1)) * W
  const esy = (v: number) => Y_PAD + (1 - (v - eMin) / eRange) * (EH - 2 * Y_PAD)
  const ezy = esy(0)
  const ePts = equity.map((v, i) => `${esx(i).toFixed(1)},${esy(v).toFixed(1)}`).join(' ')
  const eArea = `M0,${ezy.toFixed(1)} ` + equity.map((v, i) => `L${esx(i).toFixed(1)},${esy(v).toFixed(1)}`).join(' ') + ` L${esx(equity.length - 1).toFixed(1)},${ezy.toFixed(1)} Z`
  const yTicks: number[] = eMin < 0 && eMax > 0 ? [eMin, eMin / 2, 0, eMax] : [eMin, eMin + eRange / 2, eMax]
  const n = equity.length - 1
  const xTicks = [0, Math.round(n / 2), n].filter((v, i, a) => a.indexOf(v) === i)
  const ddArr: number[] = []; let peak = 0
  for (const v of equity) { peak = Math.max(peak, v); ddArr.push(v - peak) }
  const ddMin = Math.min(-1, ...ddArr)
  const dsy = (v: number) => 4 + (1 - (v - ddMin) / (-ddMin)) * (DH - 24)
  const ddPts = ddArr.map((v, i) => `${esx(i).toFixed(1)},${dsy(v).toFixed(1)}`).join(' ')
  const ddArea = `M0,4 ` + ddArr.map((v, i) => `L${esx(i).toFixed(1)},${dsy(v).toFixed(1)}`).join(' ') + ` L${esx(ddArr.length - 1).toFixed(1)},4 Z`
  const lastEq = equity[equity.length - 1]
  const lastX = esx(equity.length - 1) / W * 100
  const tradePnl = equity.map((v, i) => i === 0 ? v : v - equity[i - 1])
  function handleMouseMove(e: React.MouseEvent<HTMLDivElement>) {
    if (!chartRef.current) return
    const rect = chartRef.current.getBoundingClientRect()
    setHoverIdx(Math.round(Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)) * (equity.length - 1)))
  }
  return (
    <div className="space-y-1">
      <div className="flex gap-0">
        <div className="relative shrink-0" style={{ width: 56, height: EH }}>
          {yTicks.map(v => {
            const top = yPct(v)
            if (top < 2 || top > 97) return null
            return (
              <div key={v} className="absolute right-2 -translate-y-1/2 text-[10px] font-mono leading-none whitespace-nowrap"
                style={{ top: `${top}%`, color: v === 0 ? (isDark ? '#374151' : '#64748b') : (isDark ? '#283047' : '#94a3b8') }}>
                {fmtAxY(v)}
              </div>
            )
          })}
        </div>
        <div ref={chartRef} className="relative flex-1" style={{ height: EH }}
          onMouseMove={handleMouseMove} onMouseLeave={() => setHoverIdx(null)}>
          <svg viewBox={`0 0 ${W} ${EH}`} className="absolute inset-0 w-full h-full" preserveAspectRatio="none">
            <defs>
              <linearGradient id="eg" x1="0" x2="0" y1="0" y2="1">
                {isUp ? <><stop offset="0%" stopColor="#22c55e" stopOpacity="0.28"/><stop offset="100%" stopColor="#22c55e" stopOpacity="0"/></>
                      : <><stop offset="0%" stopColor="#ef4444" stopOpacity="0"/><stop offset="100%" stopColor="#ef4444" stopOpacity="0.22"/></>}
              </linearGradient>
              <filter id="glow"><feGaussianBlur stdDeviation="2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
            </defs>
            {yTicks.map(v => {
              const y = esy(v)
              if (y < 4 || y > EH - 4) return null
              return <line key={v} x1="0" y1={y.toFixed(1)} x2={W} y2={y.toFixed(1)}
                stroke={v === 0 ? (isDark ? '#252e45' : '#cbd5e1') : (isDark ? '#1a2130' : '#e8edf4')}
                strokeWidth={v === 0 ? 1.5 : 1} strokeDasharray={v === 0 ? '8 5' : undefined} />
            })}
            <path d={eArea} fill="url(#eg)" />
            <polyline points={ePts} fill="none" stroke={eColor} strokeWidth="1.5" opacity="0.35" filter="url(#glow)" />
            <polyline points={ePts} fill="none" stroke={eColor} strokeWidth="2.5" strokeLinejoin="round" />
            <circle cx={esx(equity.length - 1).toFixed(1)} cy={esy(lastEq).toFixed(1)} r="14" fill={eColor} opacity="0.07" />
            <circle cx={esx(equity.length - 1).toFixed(1)} cy={esy(lastEq).toFixed(1)} r="6"  fill={eColor} opacity="0.2" />
            <circle cx={esx(equity.length - 1).toFixed(1)} cy={esy(lastEq).toFixed(1)} r="3.5" fill={eColor} />
            {hoverIdx !== null && (
              <>
                <line x1={esx(hoverIdx).toFixed(1)} y1="0" x2={esx(hoverIdx).toFixed(1)} y2={String(EH)}
                  stroke={isDark ? '#ffffff14' : '#00000014'} strokeWidth="1" strokeDasharray="4 3" />
                <circle cx={esx(hoverIdx).toFixed(1)} cy={esy(equity[hoverIdx]).toFixed(1)} r="10" fill={eColor} opacity="0.12" />
                <circle cx={esx(hoverIdx).toFixed(1)} cy={esy(equity[hoverIdx]).toFixed(1)} r="4.5" fill={eColor} />
                <circle cx={esx(hoverIdx).toFixed(1)} cy={esy(equity[hoverIdx]).toFixed(1)} r="2" fill={isDark ? 'white' : '#1e293b'} opacity="0.85" />
              </>
            )}
          </svg>
          <div className="absolute text-[11px] font-mono font-bold leading-none pointer-events-none"
            style={{ top: `${yPct(lastEq)}%`, left: lastX > 75 ? undefined : `${lastX}%`, right: lastX > 75 ? '4px' : undefined, transform: 'translateY(calc(-100% - 6px))', color: eColor }}>
            {lastEq >= 0 ? '+' : ''}{fmtAxY(lastEq)}
          </div>
          {hoverIdx !== null && (() => {
            const hx = hoverIdx / (equity.length - 1) * 100
            const hy = yPct(equity[hoverIdx])
            const tp = tradePnl[hoverIdx]; const cum = equity[hoverIdx]
            return (
              <div className="absolute z-20 pointer-events-none"
                style={{ left: hx <= 60 ? `calc(${hx}% + 14px)` : undefined, right: hx > 60 ? `calc(${100 - hx}% + 14px)` : undefined, top: hy < 55 ? `calc(${hy}% + 8px)` : `calc(${hy}% - 72px)` }}>
                <div className="rounded-lg border border-white/[0.10] bg-[#0b1120]/96 px-3 py-2 shadow-2xl space-y-0.5 min-w-[140px]">
                  <div className="text-[9px] uppercase tracking-wider text-gray-600 font-bold">Trade #{hoverIdx + 1}</div>
                  <div className="text-[9px] text-gray-600 font-mono">{tsArr[hoverIdx] ? fmtDateTime(tsArr[hoverIdx]) : ''}</div>
                  <div className={cn('font-mono font-bold text-base', tp >= 0 ? 'text-green-400' : 'text-red-400')}>
                    {tp >= 0 ? '+' : ''}{fmtInr(tp)}
                  </div>
                  <div className="text-[10px] font-mono text-gray-600 border-t border-white/[0.06] pt-1 mt-0.5">
                    Cumulative: <span className={cum >= 0 ? 'text-green-600' : 'text-red-600'}>{cum >= 0 ? '+' : ''}{fmtAxY(cum)}</span>
                  </div>
                </div>
              </div>
            )
          })()}
        </div>
      </div>
      <div className="flex justify-between text-[10px] font-mono text-gray-700 pl-14 pr-1">
        {xTicks.map((i, j) => (
          <span key={i} className={j === 1 ? 'text-center' : j === 2 ? 'text-right' : ''}>{tsArr[i] ? fmtDay(tsArr[i]) : ''}</span>
        ))}
      </div>
      <div className="flex gap-0">
        <div className="shrink-0 flex items-center justify-end pr-2" style={{ width: 56 }}>
          <span className="text-[9px] font-bold uppercase tracking-wider text-gray-700">DD</span>
        </div>
        <div className="relative flex-1" style={{ height: DH }}>
          <svg viewBox={`0 0 ${W} ${DH}`} className="absolute inset-0 w-full h-full" preserveAspectRatio="none">
            <path d={ddArea} fill="#ef444414" />
            <polyline points={ddPts} fill="none" stroke="#ef4444" strokeWidth="1.5" strokeLinejoin="round" opacity="0.55" />
          </svg>
        </div>
      </div>
    </div>
  )
}

// ── Calendar Heatmap ──────────────────────────────────────────────────────────
function CalHeatmap({ dailyPnl, isDark }: { dailyPnl: { day: string; pnl: number }[]; isDark: boolean }) {
  const defaultYM = useMemo(() => {
    if (!dailyPnl.length) { const now = new Date(); return { y: now.getFullYear(), m: now.getMonth() } }
    const counts: Record<string, number> = {}
    for (const d of dailyPnl) { const k = d.day.slice(0, 7); counts[k] = (counts[k] || 0) + 1 }
    const best = Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0]
    const [y, m] = best.split('-').map(Number)
    return { y, m: m - 1 }
  }, [dailyPnl])
  const [viewYear, setViewYear] = useState(defaultYM.y)
  const [viewMonth, setViewMonth] = useState(defaultYM.m)
  const monthsWithData = useMemo(() => { const s = new Set<string>(); for (const d of dailyPnl) s.add(d.day.slice(0, 7)); return s }, [dailyPnl])
  function ym(y: number, m: number) { return `${y}-${String(m + 1).padStart(2, '0')}` }
  const prevYM = viewMonth === 0 ? { y: viewYear - 1, m: 11 } : { y: viewYear, m: viewMonth - 1 }
  const nextYM = viewMonth === 11 ? { y: viewYear + 1, m: 0 } : { y: viewYear, m: viewMonth + 1 }
  const hasPrev = monthsWithData.has(ym(prevYM.y, prevYM.m))
  const hasNext = monthsWithData.has(ym(nextYM.y, nextYM.m))
  const dayMap: Record<string, number> = {}
  for (const d of dailyPnl) dayMap[d.day] = d.pnl
  const maxAbs = Math.max(...dailyPnl.map(d => Math.abs(d.pnl)), 1)
  const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate()
  const firstDow = (new Date(viewYear, viewMonth, 1).getDay() + 6) % 7
  const cells: ({ day: number; dateStr: string; pnl: number | null } | null)[] = [
    ...Array<null>(firstDow).fill(null),
    ...Array.from({ length: daysInMonth }, (_, i) => {
      const d = i + 1
      const dateStr = `${viewYear}-${String(viewMonth + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`
      return { day: d, dateStr, pnl: dayMap[dateStr] ?? null }
    }),
  ]
  while (cells.length % 7 !== 0) cells.push(null)
  function cellBg(pnl: number | null, isWeekend: boolean): string {
    if (pnl !== null) {
      const intensity = Math.min(1, Math.abs(pnl) / maxAbs)
      return pnl > 0 ? `rgba(34,197,94,${0.12 + intensity * 0.65})` : `rgba(239,68,68,${0.12 + intensity * 0.65})`
    }
    return isWeekend ? (isDark ? 'rgba(255,255,255,0.012)' : 'rgba(0,0,0,0.025)') : (isDark ? 'rgba(255,255,255,0.028)' : 'rgba(0,0,0,0.04)')
  }
  const monthLabel = new Date(viewYear, viewMonth, 1).toLocaleString('en-IN', { month: 'long', year: 'numeric' })
  const tradeCount = cells.filter(c => c?.pnl != null).length
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <button onClick={() => { setViewYear(prevYM.y); setViewMonth(prevYM.m) }} disabled={!hasPrev}
          className={cn('text-[11px] px-2 py-0.5 rounded', hasPrev ? 'text-gray-400 hover:text-white hover:bg-white/[0.06]' : 'text-gray-800 cursor-default')}>
          ← prev
        </button>
        <div className="text-[10px] font-semibold text-gray-500">
          {monthLabel} <span className="ml-2 text-gray-700 font-normal">{tradeCount} trade days</span>
        </div>
        <button onClick={() => { setViewYear(nextYM.y); setViewMonth(nextYM.m) }} disabled={!hasNext}
          className={cn('text-[11px] px-2 py-0.5 rounded', hasNext ? 'text-gray-400 hover:text-white hover:bg-white/[0.06]' : 'text-gray-800 cursor-default')}>
          next →
        </button>
      </div>
      <div className="grid grid-cols-7 gap-0.5">
        {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d, i) => (
          <div key={i} className="text-center text-[9px] font-bold text-gray-700 pb-1">{d}</div>
        ))}
        {cells.map((c, i) => {
          const isWeekend = (i % 7) >= 5
          return (
            <div key={i} className="h-8 rounded flex flex-col items-center justify-center"
              style={{ background: c ? cellBg(c.pnl, isWeekend) : 'transparent', border: c ? '1px solid rgba(255,255,255,0.05)' : 'none', opacity: c && c.pnl == null && isWeekend ? 0.45 : 1 }}>
              {c && (
                <>
                  <span className="text-[10px] font-bold leading-none"
                    style={{ color: c.pnl == null ? (isWeekend ? '#1f2937' : '#2d3748') : c.pnl > 0 ? '#4ade80' : '#f87171' }}>
                    {c.day}
                  </span>
                  {c.pnl != null && (
                    <span className="text-[8px] font-mono leading-none mt-0.5"
                      style={{ color: c.pnl > 0 ? '#22c55e99' : '#ef444499' }}>
                      {Math.abs(c.pnl) >= 1000 ? `${c.pnl > 0 ? '+' : '-'}${(Math.abs(c.pnl) / 1000).toFixed(0)}K` : `${c.pnl > 0 ? '+' : ''}${Math.round(c.pnl)}`}
                    </span>
                  )}
                </>
              )}
            </div>
          )
        })}
      </div>
      <div className="flex items-center gap-2 text-[9px] text-gray-700">
        <div className="w-2.5 h-2.5 rounded-sm" style={{ background: 'rgba(239,68,68,0.65)' }} /><span>Loss</span>
        <div className="w-2.5 h-2.5 rounded-sm ml-2" style={{ background: 'rgba(34,197,94,0.65)' }} /><span>Profit</span>
        <span className="ml-auto text-gray-800">darker = bigger</span>
      </div>
    </div>
  )
}

// ── Win Donut ─────────────────────────────────────────────────────────────────
function WinDonut({ wins, losses, isDark }: { wins: number; losses: number; isDark: boolean }) {
  const total = wins + losses; const pct = total > 0 ? wins / total : 0
  const R = 36, CX = 44, CY = 44, SW = 8
  const circ = 2 * Math.PI * R; const winLen = pct * circ
  const color = pct >= 0.5 ? '#22c55e' : '#ef4444'
  return (
    <svg viewBox="0 0 88 88" className="w-20 h-20">
      <circle cx={CX} cy={CY} r={R} fill="none" stroke={isDark ? '#161d2d' : '#e2e8f0'} strokeWidth={SW} />
      {total > 0 && <circle cx={CX} cy={CY} r={R} fill="none" stroke={color} strokeWidth={SW}
        strokeDasharray={`${winLen.toFixed(2)} ${(circ - winLen).toFixed(2)}`}
        strokeLinecap="round" transform={`rotate(-90 ${CX} ${CY})`} />}
      <text x={CX} y={CY - 4} textAnchor="middle" fill={isDark ? '#fff' : '#0f172a'} fontSize="16" fontWeight="700" fontFamily="ui-monospace,monospace">
        {Math.round(pct * 100)}%
      </text>
      <text x={CX} y={CY + 9} textAnchor="middle" fill={isDark ? '#374151' : '#94a3b8'} fontSize="7" letterSpacing="1.2" fontFamily="system-ui">WIN RATE</text>
    </svg>
  )
}

// ── Daily P&L Histogram ───────────────────────────────────────────────────────
function DailyHistogram({ values, isDark }: { values: number[]; isDark: boolean }) {
  if (values.length < 2) return (
    <div className="h-28 flex items-center justify-center text-xs" style={{ color: isDark ? '#374151' : '#94a3b8' }}>
      Need 2+ trade days
    </div>
  )
  const BINS = 12
  const lo = Math.min(...values), hi = Math.max(...values)
  const binW = (hi - lo) / BINS || 1
  const bins = Array.from({ length: BINS }, (_, i) => {
    const blo = lo + i * binW, bhi = lo + (i + 1) * binW
    return { lo: blo, hi: bhi, mid: (blo + bhi) / 2, count: 0 }
  })
  for (const v of values) { const i = Math.min(BINS - 1, Math.floor((v - lo) / binW)); bins[i].count++ }
  const maxCount = Math.max(...bins.map(b => b.count), 1)
  const W = 400, H = 80
  const zx = lo < 0 && hi > 0 ? ((-lo) / (hi - lo)) * W : null
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H + 18}`} className="w-full">
        {zx !== null && <line x1={zx.toFixed(1)} y1="0" x2={zx.toFixed(1)} y2={H} stroke={isDark ? '#252e45' : '#cbd5e1'} strokeWidth={1} />}
        {bins.map((b, i) => {
          const x = (i / BINS) * W + 1
          const bw = W / BINS - 2
          const bh = (b.count / maxCount) * (H - 4)
          return (
            <rect key={i} x={x.toFixed(1)} y={(H - bh).toFixed(1)} width={bw.toFixed(1)} height={bh.toFixed(1)}
              fill={b.mid >= 0 ? 'rgba(34,197,94,0.72)' : 'rgba(239,68,68,0.72)'} rx="1" />
          )
        })}
        <line x1="0" y1={H} x2={W} y2={H} stroke={isDark ? '#1a2130' : '#e2e8f0'} strokeWidth={0.5} />
        <text x="2" y={H + 13} fontSize="7" fill={isDark ? '#374151' : '#94a3b8'} fontFamily="ui-monospace,monospace">{fmtAxY(lo)}</text>
        {zx !== null && <text x={(zx + 3).toFixed(1)} y={H + 13} fontSize="7" fill={isDark ? '#374151' : '#94a3b8'} fontFamily="ui-monospace,monospace">0</text>}
        <text x={W - 2} y={H + 13} textAnchor="end" fontSize="7" fill={isDark ? '#374151' : '#94a3b8'} fontFamily="ui-monospace,monospace">{fmtAxY(hi)}</text>
      </svg>
    </div>
  )
}

// ── Rolling Sharpe chart ──────────────────────────────────────────────────────
function RollingSharpeChart({ dailyPnl, isDark }: { dailyPnl: { day: string; pnl: number }[]; isDark: boolean }) {
  const WIN = Math.max(5, Math.min(30, Math.floor(dailyPnl.length / 2)))
  const points = useMemo(() => {
    const pts: { day: string; sharpe: number }[] = []
    for (let i = WIN - 1; i < dailyPnl.length; i++) {
      const window = dailyPnl.slice(i - WIN + 1, i + 1).map(d => d.pnl)
      const sh = annualSharpe(window)
      if (sh !== null && Number.isFinite(sh)) pts.push({ day: dailyPnl[i].day, sharpe: sh })
    }
    return pts
  }, [dailyPnl, WIN])

  if (points.length < 2) return (
    <div className="h-24 flex items-center justify-center text-xs" style={{ color: isDark ? '#374151' : '#94a3b8' }}>
      Need {WIN}+ trading days for rolling Sharpe
    </div>
  )
  const W = 1000, H = 72
  const shVals = points.map(p => p.sharpe)
  const shMin = Math.min(-0.5, ...shVals), shMax = Math.max(0.5, ...shVals)
  const shRange = shMax - shMin || 1
  const sx = (i: number) => (i / (points.length - 1)) * W
  const sy = (v: number) => 4 + (1 - (v - shMin) / shRange) * (H - 8)
  const zy = sy(0)
  const pts = points.map((p, i) => `${sx(i).toFixed(1)},${sy(p.sharpe).toFixed(1)}`).join(' ')
  const avgSh = arrMean(shVals)
  const lineColor = avgSh >= 1 ? '#22c55e' : avgSh >= 0 ? '#eab308' : '#ef4444'

  const areaPath = `M${sx(0).toFixed(1)},${zy.toFixed(1)} ` +
    points.map((p, i) => `L${sx(i).toFixed(1)},${sy(p.sharpe).toFixed(1)}`).join(' ') +
    ` L${sx(points.length - 1).toFixed(1)},${zy.toFixed(1)} Z`

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 80 }} preserveAspectRatio="none">
        <defs>
          <linearGradient id="shg" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={lineColor} stopOpacity="0.18" />
            <stop offset="100%" stopColor={lineColor} stopOpacity="0" />
          </linearGradient>
        </defs>
        <line x1="0" y1={zy.toFixed(1)} x2={W} y2={zy.toFixed(1)} stroke={isDark ? '#252e45' : '#cbd5e1'} strokeWidth={1} strokeDasharray="6 4" />
        {[1, -1, 2, -2].map(ref => {
          const y = sy(ref); if (y < 0 || y > H) return null
          return <line key={ref} x1="0" y1={y.toFixed(1)} x2={W} y2={y.toFixed(1)}
            stroke={ref > 0 ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)'} strokeWidth={1} strokeDasharray="3 3" />
        })}
        <path d={areaPath} fill="url(#shg)" />
        <polyline points={pts} fill="none" stroke={lineColor} strokeWidth="2" strokeLinejoin="round" />
        <circle cx={sx(points.length - 1).toFixed(1)} cy={sy(points[points.length - 1].sharpe).toFixed(1)} r="4" fill={lineColor} />
      </svg>
      <div className="flex justify-between text-[9px] font-mono mt-1" style={{ color: isDark ? '#374151' : '#94a3b8' }}>
        <span>{fmtDay(points[0].day)}</span>
        <span>Rolling {WIN}-day Sharpe (annualised)</span>
        <span>{fmtDay(points[points.length - 1].day)}</span>
      </div>
    </div>
  )
}

// ── Horizontal bar chart (pod / strategy) ────────────────────────────────────
function HBarChart({ data, isDark }: { data: { label: string; pnl: number; n: number }[]; isDark: boolean }) {
  const maxAbs = Math.max(...data.map(d => Math.abs(d.pnl)), 1)
  return (
    <div className="space-y-2">
      {data.map(d => (
        <div key={d.label} className="grid items-center gap-2" style={{ gridTemplateColumns: '110px 1fr 72px' }}>
          <div className="text-[10px] font-mono truncate text-right" style={{ color: isDark ? '#9ca3af' : '#475569' }}>
            {d.label.replace(/_/g, ' ')}
          </div>
          <div className="h-5 rounded overflow-hidden" style={{ background: isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.06)' }}>
            <div className="h-full rounded" style={{ width: `${Math.abs(d.pnl) / maxAbs * 100}%`, background: d.pnl >= 0 ? 'rgba(34,197,94,0.7)' : 'rgba(239,68,68,0.7)' }} />
          </div>
          <div className={cn('text-[11px] font-mono font-bold text-right', d.pnl >= 0 ? 'text-green-500' : 'text-red-500')}>
            {d.pnl >= 0 ? '+' : ''}{fmtCr(d.pnl)}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Metric tile ───────────────────────────────────────────────────────────────
function Tile({ label, value, sub, accent, small }: {
  label: string; value: string; sub?: string; accent?: string; small?: boolean
}) {
  return (
    <div className="relative rounded-xl border border-white/[0.06] bg-[#0d1117] px-4 py-3.5 overflow-hidden">
      {accent && <div className="absolute top-0 left-0 right-0 h-[2px]" style={{ background: accent }} />}
      <div className="text-[10px] uppercase tracking-[0.12em] text-gray-600 font-semibold mb-1.5">{label}</div>
      <div className={cn('font-mono font-bold leading-none text-white', small ? 'text-lg' : 'text-xl')}>{value}</div>
      {sub && <div className="text-[10px] text-gray-700 font-mono mt-1 leading-tight">{sub}</div>}
    </div>
  )
}

function SH({ label, right }: { label: string; right?: string }) {
  return (
    <div className="flex items-center justify-between mb-3">
      <div className="flex items-center gap-2.5">
        <div className="h-px w-5 bg-gray-800" />
        <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-600">{label}</span>
      </div>
      {right && <span className="text-[10px] text-gray-700 font-mono">{right}</span>}
    </div>
  )
}

function Bar({ val, max }: { val: number; max: number }) {
  const pct = max > 0 ? Math.min(100, Math.abs(val) / max * 100) : 0
  return (
    <div className="h-1 bg-white/[0.04] rounded-full overflow-hidden w-20">
      <div className={cn('h-full rounded-full', val >= 0 ? 'bg-green-600' : 'bg-red-600')} style={{ width: `${pct}%` }} />
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
export default function ReportsPage() {
  const isDark = useDarkMode()
  const [period, setPeriod] = useState<Period>('all')

  const { data: allTrades = [], isLoading } = useQuery<Trade[]>({
    queryKey: ['trades'],
    queryFn: () => fetchJson('/portfolio/trades'),
    refetchInterval: 30_000,
  })
  const { data: capital } = useQuery<CapitalSnapshot>({
    queryKey: ['capital-snapshot'],
    queryFn: () => fetchJson('/portfolio/snapshot'),
    refetchInterval: 30_000,
  })

  const start = periodStart(period)
  const trades = useMemo(() => allTrades.filter(t => inPeriod(t, start)), [allTrades, period, start])
  const closed = useMemo(() => trades.filter(t => t.entry_price != null), [trades])
  const sortedClosed = useMemo(() => [...closed].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()), [closed])

  // ── Core stats ──────────────────────────────────────────────────────────────
  const winners     = closed.filter(t => t.pnl > 0)
  const losers      = closed.filter(t => t.pnl < 0)
  const totalPnl    = closed.reduce((s, t) => s + t.pnl, 0)
  const grossProfit = winners.reduce((s, t) => s + t.pnl, 0)
  const grossLoss   = Math.abs(losers.reduce((s, t) => s + t.pnl, 0))
  const winRate     = closed.length ? winners.length / closed.length : 0
  const pf          = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? 99 : 0
  const avgWin      = winners.length ? grossProfit / winners.length : 0
  const avgLoss     = losers.length ? grossLoss / losers.length : 0
  const rr          = avgLoss > 0 ? avgWin / avgLoss : 0
  const expectancy  = winRate * avgWin - (1 - winRate) * avgLoss
  const turnover    = closed.reduce((s, t) => s + t.quantity * t.price, 0)
  const totalCap    = capital ? parseFloat(capital.total_capital) : 0
  const returnPct   = totalCap > 0 ? (totalPnl / totalCap) * 100 : 0

  // ── Equity / drawdown ────────────────────────────────────────────────────────
  const equityVals = useMemo(() => { let cum = 0; return sortedClosed.map(t => (cum += t.pnl)) }, [sortedClosed])
  const equityTs   = useMemo(() => sortedClosed.map(t => t.timestamp), [sortedClosed])

  const { maxDD, maxDDPct } = useMemo(() => {
    let peak = 0, dd = 0
    for (const v of equityVals) { peak = Math.max(peak, v); dd = Math.max(dd, peak - v) }
    const pct = totalCap > 0 ? (dd / (totalCap + Math.max(0, equityVals[0] ?? 0))) * 100 : 0
    return { maxDD: dd, maxDDPct: pct }
  }, [equityVals, totalCap])

  const { maxW, maxL } = useMemo(() => {
    let cW = 0, cL = 0, mW = 0, mL = 0
    for (const t of sortedClosed) {
      if (t.pnl > 0) { cW++; cL = 0; mW = Math.max(mW, cW) }
      else           { cL++; cW = 0; mL = Math.max(mL, cL) }
    }
    return { maxW: mW, maxL: mL }
  }, [sortedClosed])

  // ── Daily P&L (for Sharpe, Sortino, heatmap, histogram) ─────────────────────
  const dailyPnl = useMemo(() => {
    const m: Record<string, number> = {}
    for (const t of closed) { const d = t.timestamp.slice(0, 10); m[d] = (m[d] || 0) + t.pnl }
    return Object.entries(m).sort().map(([day, pnl]) => ({ day, pnl }))
  }, [closed])
  const dailyPnlVals = dailyPnl.map(d => d.pnl)

  // ── Risk-adjusted metrics ────────────────────────────────────────────────────
  const sharpeVal  = annualSharpe(dailyPnlVals)
  const sortinoVal = annualSortino(dailyPnlVals)
  const cagrVal    = sortedClosed.length >= 2
    ? calcCAGR(totalPnl, totalCap, sortedClosed[0].timestamp, sortedClosed[sortedClosed.length - 1].timestamp)
    : null
  const { t: tStatVal, p: pValVal } = tStatPVal(closed.map(t => t.pnl))

  // ── Hold time stats ───────────────────────────────────────────────────────────
  const holdTimes = useMemo(() => {
    const ms: number[] = []
    for (const t of sortedClosed) {
      if (t.entry_time) {
        const d = new Date(t.timestamp).getTime() - new Date(t.entry_time).getTime()
        if (d > 0) ms.push(d)
      }
    }
    return ms
  }, [sortedClosed])

  const holdStats = useMemo(() => {
    if (!holdTimes.length) return null
    const sorted = [...holdTimes].sort((a, b) => a - b)
    return {
      avg: arrMean(holdTimes),
      med: medianOf(holdTimes),
      min: sorted[0],
      max: sorted[sorted.length - 1],
    }
  }, [holdTimes])

  // ── By symbol ────────────────────────────────────────────────────────────────
  const bySymbol = useMemo(() => {
    const m: Record<string, { sym: string; n: number; pnl: number; wins: number; tv: number }> = {}
    for (const t of closed) {
      if (!m[t.symbol]) m[t.symbol] = { sym: t.symbol, n: 0, pnl: 0, wins: 0, tv: 0 }
      m[t.symbol].n++; m[t.symbol].pnl += t.pnl; m[t.symbol].tv += t.quantity * t.price
      if (t.pnl > 0) m[t.symbol].wins++
    }
    return Object.values(m).sort((a, b) => b.pnl - a.pnl)
  }, [closed])

  // ── By pod ───────────────────────────────────────────────────────────────────
  const byPod = useMemo(() => {
    const m: Record<string, { label: string; pnl: number; n: number }> = {}
    for (const t of closed) {
      const k = t.source_pod || t.source_desk || 'unknown'
      if (!m[k]) m[k] = { label: k, pnl: 0, n: 0 }
      m[k].pnl += t.pnl; m[k].n++
    }
    return Object.values(m).sort((a, b) => b.pnl - a.pnl)
  }, [closed])

  // ── By strategy ──────────────────────────────────────────────────────────────
  const byStrategy = useMemo(() => {
    const m: Record<string, { label: string; pnl: number; n: number }> = {}
    for (const t of closed) {
      const k = t.strategy || 'untagged'
      if (!m[k]) m[k] = { label: k, pnl: 0, n: 0 }
      m[k].pnl += t.pnl; m[k].n++
    }
    return Object.values(m).sort((a, b) => b.pnl - a.pnl)
  }, [closed])

  const maxSymPnl  = bySymbol.length ? Math.max(...bySymbol.map(r => Math.abs(r.pnl))) : 1
  const bestTrade  = sortedClosed.length ? sortedClosed.reduce((a, b) => a.pnl > b.pnl ? a : b) : null
  const worstTrade = sortedClosed.length ? sortedClosed.reduce((a, b) => a.pnl < b.pnl ? a : b) : null
  const isUp       = totalPnl >= 0
  const pnlColor   = isUp ? '#22c55e' : '#ef4444'
  const profitDays = dailyPnl.filter(d => d.pnl > 0).length
  const lossDays   = dailyPnl.filter(d => d.pnl < 0).length
  const smallSample = closed.length < 100

  const ACCENT = {
    good:   '#22c55e',
    warn:   '#eab308',
    bad:    '#ef4444',
    blue:   '#3b82f6',
    purple: '#a855f7',
    neutral:'#374151',
  }

  return (
    <>
      <style>{`@keyframes ri{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}.ri{animation:ri 0.3s ease both}`}</style>

      {/* ── HEADER + PERIOD SELECTOR ────────────────────────────────────────── */}
      <div className="-mx-6 -mt-6 px-6 py-3 border-b border-white/[0.06] bg-[#090d13] flex items-center justify-between gap-4 flex-wrap">
        <div>
          <div className="text-[10px] uppercase tracking-[0.18em] text-gray-700 font-bold">MoneyMaker · NSE / BSE · Paper</div>
          <div className="text-sm font-bold text-white tracking-tight">Performance Report</div>
        </div>
        <div className="flex items-center gap-3">
          {capital && (
            <div className="hidden md:flex items-center gap-3 text-[10px] font-mono text-gray-700 border-r border-white/[0.06] pr-4">
              <span>Capital <span className="text-gray-500">{fmtCr(parseFloat(capital.total_capital))}</span></span>
              <span>Avail <span className="text-gray-500">{fmtCr(parseFloat(capital.available_capital))}</span></span>
            </div>
          )}
          <div className="flex rounded-lg border border-white/[0.08] overflow-hidden bg-[#0d1117] text-xs">
            {PERIODS.map(p => (
              <button key={p.key} onClick={() => setPeriod(p.key)}
                className={cn('px-3 py-2 font-semibold transition-all', period === p.key ? '' : 'text-gray-600 hover:text-gray-400')}
                style={period === p.key ? { color: pnlColor, background: `${pnlColor}18` } : {}}>
                {p.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="-mx-6 px-6 pb-16 pt-0 space-y-5 bg-[#090d13] min-h-screen">

        {/* ── SAMPLE SIZE WARNING ────────────────────────────────────────────── */}
        {!isLoading && closed.length > 0 && smallSample && (
          <div className="flex items-center gap-3 rounded-lg border border-yellow-900/40 bg-yellow-950/20 px-4 py-2.5 text-xs text-yellow-500 ri">
            <span className="text-base">⚠</span>
            <span>Statistical metrics below are based on <strong>{closed.length}</strong> trades — confidence improves significantly beyond 100. Treat Sharpe, T-stat, and P-value as directional only.</span>
          </div>
        )}

        {/* ── HERO: P&L SUMMARY + EQUITY CURVE ───────────────────────────────── */}
        <div className="grid grid-cols-1 lg:grid-cols-[260px_1fr] border-b border-white/[0.05]">
          <div className="px-0 py-5 pr-6 space-y-4 border-r border-white/[0.05] ri">
            <div>
              <div className="text-[10px] uppercase tracking-[0.18em] text-gray-600 font-semibold mb-1">
                Net P&L · {PERIODS.find(p => p.key === period)?.label}
              </div>
              <div className="font-mono font-black leading-none"
                style={{ fontSize: 38, color: closed.length === 0 ? '#374151' : pnlColor }}>
                {closed.length === 0 ? '—' : `${totalPnl >= 0 ? '+' : ''}${fmtInr(totalPnl)}`}
              </div>
              {totalCap > 0 && closed.length > 0 && (
                <div className="text-sm font-mono font-bold mt-1" style={{ color: `${pnlColor}bb` }}>
                  {returnPct >= 0 ? '+' : ''}{returnPct.toFixed(2)}% on capital
                </div>
              )}
            </div>
            <div className="flex items-center gap-4">
              <WinDonut wins={winners.length} losses={losers.length} isDark={isDark} />
              <div className="space-y-1.5">
                <div className="text-sm font-mono font-bold text-white">{closed.length} trades</div>
                <div className="flex gap-3 text-sm font-mono font-bold">
                  <span className="text-green-500">{winners.length}W</span>
                  <span className="text-gray-700">/</span>
                  <span className="text-red-500">{losers.length}L</span>
                </div>
                <div className="text-[10px] font-mono text-gray-700">
                  <span className="text-green-700">{profitDays}d profit</span>
                  <span className="mx-1">/</span>
                  <span className="text-red-700">{lossDays}d loss</span>
                </div>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {[
                { label: 'Avg Win',  val: avgWin > 0  ? `+${fmtPrice(avgWin)}`  : '—', c: 'text-green-500' },
                { label: 'Avg Loss', val: avgLoss > 0 ? `-${fmtPrice(avgLoss)}` : '—', c: 'text-red-500'   },
                { label: 'Gross P',  val: grossProfit > 0 ? fmtCr(grossProfit)  : '—', c: 'text-green-700' },
                { label: 'Gross L',  val: grossLoss  > 0 ? fmtCr(grossLoss)    : '—', c: 'text-red-700'   },
              ].map(r => (
                <div key={r.label} className="rounded-lg bg-[#0d1117] border border-white/[0.04] px-3 py-2">
                  <div className="text-[9px] uppercase tracking-wider text-gray-700 mb-0.5">{r.label}</div>
                  <div className={cn('font-mono font-bold text-sm', r.c)}>{r.val}</div>
                </div>
              ))}
            </div>
            <div className="text-[10px] font-mono text-gray-700 pt-1 border-t border-white/[0.04]">
              Turnover {fmtCr(turnover)}
            </div>
          </div>

          <div className="py-5 pl-5 ri" style={{ animationDelay: '0.06s' }}>
            <div className="flex items-center justify-between mb-2">
              <div className="text-[10px] uppercase tracking-[0.16em] text-gray-600 font-semibold">Equity Curve &amp; Drawdown</div>
              {equityTs.length >= 2 && (
                <div className="text-[10px] text-gray-700 font-mono">
                  {fmtDay(equityTs[0])} → {fmtDay(equityTs[equityTs.length - 1])}
                  <span className="ml-2 text-gray-800">({equityVals.length} trades)</span>
                </div>
              )}
            </div>
            <div className="rounded-xl border border-white/[0.05] bg-[#0a0f18] p-3">
              {closed.length >= 2
                ? <EquityDrawdown equity={equityVals} tsArr={equityTs} isUp={isUp} isDark={isDark} />
                : <div className="h-56 flex items-center justify-center text-gray-700 text-xs">
                    {closed.length === 0 ? 'No closed trades — switch to "All Time"' : 'Complete 2+ trades to see curve'}
                  </div>}
            </div>
          </div>
        </div>

        {isLoading && (
          <div className="grid grid-cols-4 gap-3">
            {[...Array(8)].map((_, i) => <div key={i} className="h-16 rounded-xl bg-white/[0.02] animate-pulse border border-white/[0.04]" />)}
          </div>
        )}

        {!isLoading && closed.length === 0 && (
          <div className="flex flex-col items-center justify-center py-20 space-y-2">
            <div className="text-3xl opacity-20">📊</div>
            <div className="text-gray-600 text-sm font-medium">No closed trades in this period</div>
            <button onClick={() => setPeriod('all')} className="text-xs text-blue-600 hover:text-blue-400 mt-1 underline underline-offset-2">
              Switch to All Time
            </button>
          </div>
        )}

        {closed.length > 0 && (
          <>
            {/* ── KPI STRIP ────────────────────────────────────────────────── */}
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2 ri" style={{ animationDelay: '0.08s' }}>
              <Tile label="Sharpe" value={fmtStat(sharpeVal)}
                sub="annualised" small
                accent={sharpeVal === null ? ACCENT.neutral : sharpeVal >= 2 ? ACCENT.good : sharpeVal >= 1 ? ACCENT.warn : ACCENT.bad} />
              <Tile label="Sortino" value={fmtStat(sortinoVal)}
                sub="downside vol" small
                accent={sortinoVal === null ? ACCENT.neutral : sortinoVal >= 2 ? ACCENT.good : sortinoVal >= 1 ? ACCENT.warn : ACCENT.bad} />
              <Tile label="Max DD"
                value={maxDD > 0 ? `-${maxDDPct.toFixed(1)}%` : '0%'}
                sub={maxDD > 0 ? fmtInr(maxDD) : 'None'}
                accent={maxDDPct > 15 ? ACCENT.bad : maxDDPct > 5 ? ACCENT.warn : ACCENT.good} />
              <Tile label="CAGR"
                value={cagrVal === null ? '—' : `${cagrVal >= 0 ? '+' : ''}${(cagrVal * 100).toFixed(1)}%`}
                sub="annualised return" small
                accent={cagrVal === null ? ACCENT.neutral : cagrVal >= 0.2 ? ACCENT.good : cagrVal >= 0 ? ACCENT.warn : ACCENT.bad} />
              <Tile label="Win Rate" value={`${Math.round(winRate * 100)}%`}
                sub={`${winners.length}W / ${losers.length}L`}
                accent={winRate >= 0.55 ? ACCENT.good : winRate >= 0.45 ? ACCENT.warn : ACCENT.bad} />
              <Tile label="Profit Factor" value={pf >= 99 ? '∞' : pf.toFixed(2)}
                sub={`${fmtCr(grossProfit)} / ${fmtCr(grossLoss)}`} small
                accent={pf >= 2 ? ACCENT.good : pf >= 1 ? ACCENT.warn : ACCENT.bad} />
              <Tile label="Expectancy" value={`${expectancy >= 0 ? '+' : ''}${fmtPrice(expectancy)}`}
                sub="expected per trade" small
                accent={expectancy >= 0 ? ACCENT.good : ACCENT.bad} />
              <Tile label="Trades" value={`${closed.length}`}
                sub={`${allTrades.length - closed.length} open`}
                accent={ACCENT.blue} />
            </div>

            {/* ── HEATMAP + HISTOGRAM ──────────────────────────────────────── */}
            <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-4">
              <div className="rounded-xl border border-white/[0.06] bg-[#0d1117] p-5">
                <SH label="Daily P&L Calendar" right={`${profitDays} green · ${lossDays} red`} />
                {dailyPnl.length > 0
                  ? <CalHeatmap dailyPnl={dailyPnl} isDark={isDark} />
                  : <div className="py-8 text-center text-gray-700 text-xs">No daily data</div>}
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-[#0d1117] p-5">
                <SH label="Daily P&L Distribution" right={`${dailyPnl.length} days`} />
                <DailyHistogram values={dailyPnlVals} isDark={isDark} />
                <div className="mt-3 grid grid-cols-2 gap-2">
                  {[
                    { label: 'Best Day',  val: dailyPnlVals.length ? `+${fmtCr(Math.max(...dailyPnlVals))}` : '—', c: 'text-green-500' },
                    { label: 'Worst Day', val: dailyPnlVals.length ? fmtCr(Math.min(...dailyPnlVals)) : '—', c: 'text-red-500' },
                    { label: 'Avg Day',   val: dailyPnlVals.length ? `${arrMean(dailyPnlVals) >= 0 ? '+' : ''}${fmtCr(arrMean(dailyPnlVals))}` : '—', c: 'text-gray-400' },
                    { label: 'Median',    val: dailyPnlVals.length ? `${medianOf(dailyPnlVals) >= 0 ? '+' : ''}${fmtCr(medianOf(dailyPnlVals))}` : '—', c: 'text-gray-400' },
                  ].map(r => (
                    <div key={r.label} className="rounded bg-[#090d13] border border-white/[0.04] px-2.5 py-1.5">
                      <div className="text-[9px] uppercase tracking-wider text-gray-700">{r.label}</div>
                      <div className={cn('font-mono font-bold text-xs mt-0.5', r.c)}>{r.val}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* ── ROLLING SHARPE ───────────────────────────────────────────── */}
            {dailyPnl.length >= 5 && (
              <div className="rounded-xl border border-white/[0.06] bg-[#0d1117] p-5">
                <SH label="Rolling Sharpe" right={`${dailyPnl.length} trading days`} />
                <RollingSharpeChart dailyPnl={dailyPnl} isDark={isDark} />
              </div>
            )}

            {/* ── WIN/LOSS + HOLD TIME ──────────────────────────────────────── */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {/* Win / Loss comparison */}
              <div className="rounded-xl border border-white/[0.06] bg-[#0d1117] p-5">
                <SH label="Win / Loss Breakdown" />
                <div className="space-y-3">
                  {[
                    { label: `Avg Win (${winners.length} trades)`, val: avgWin, isPos: true },
                    { label: `Avg Loss (${losers.length} trades)`, val: -avgLoss, isPos: false },
                  ].map(row => {
                    const maxVal = Math.max(avgWin, avgLoss, 1)
                    return (
                      <div key={row.label}>
                        <div className="flex justify-between text-[10px] font-mono mb-1" style={{ color: isDark ? '#6b7280' : '#94a3b8' }}>
                          <span>{row.label}</span>
                          <span className={row.isPos ? 'text-green-500 font-bold' : 'text-red-500 font-bold'}>
                            {row.isPos ? '+' : ''}{fmtPrice(row.val)}
                          </span>
                        </div>
                        <div className="h-6 rounded overflow-hidden" style={{ background: isDark ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.05)' }}>
                          <div className="h-full rounded" style={{ width: `${Math.abs(row.val) / maxVal * 100}%`, background: row.isPos ? 'rgba(34,197,94,0.7)' : 'rgba(239,68,68,0.7)' }} />
                        </div>
                      </div>
                    )
                  })}
                  <div className="grid grid-cols-3 gap-2 pt-2">
                    {[
                      { label: 'R:R Ratio',    val: `${rr.toFixed(2)}×`,       c: rr >= 1.5 ? 'text-green-500' : rr >= 1 ? 'text-yellow-500' : 'text-red-500' },
                      { label: 'Win Streak',   val: `${maxW} wins`,             c: 'text-gray-400' },
                      { label: 'Loss Streak',  val: `${maxL} losses`,           c: 'text-gray-400' },
                    ].map(r => (
                      <div key={r.label} className="rounded bg-[#090d13] border border-white/[0.04] px-2.5 py-1.5 text-center">
                        <div className="text-[9px] uppercase tracking-wider text-gray-700">{r.label}</div>
                        <div className={cn('font-mono font-bold text-sm mt-0.5', r.c)}>{r.val}</div>
                      </div>
                    ))}
                  </div>
                  {bestTrade && (
                    <div className="rounded-lg border border-green-900/25 bg-green-950/10 px-3 py-2 mt-1">
                      <div className="flex items-center justify-between">
                        <div>
                          <div className="text-[9px] font-bold uppercase tracking-wider text-green-800 mb-0.5">Best Trade</div>
                          <div className="font-mono font-bold text-white text-sm">{bestTrade.symbol}</div>
                        </div>
                        <div className="text-right">
                          <div className="text-green-400 font-mono font-bold text-lg">+{fmtPrice(bestTrade.pnl)}</div>
                          <div className="text-[9px] font-mono text-gray-700">{fmtDay(bestTrade.timestamp)}</div>
                        </div>
                      </div>
                    </div>
                  )}
                  {worstTrade && (
                    <div className="rounded-lg border border-red-900/25 bg-red-950/10 px-3 py-2">
                      <div className="flex items-center justify-between">
                        <div>
                          <div className="text-[9px] font-bold uppercase tracking-wider text-red-800 mb-0.5">Worst Trade</div>
                          <div className="font-mono font-bold text-white text-sm">{worstTrade.symbol}</div>
                        </div>
                        <div className="text-right">
                          <div className="text-red-400 font-mono font-bold text-lg">{fmtPrice(worstTrade.pnl)}</div>
                          <div className="text-[9px] font-mono text-gray-700">{fmtDay(worstTrade.timestamp)}</div>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* Hold time stats */}
              <div className="rounded-xl border border-white/[0.06] bg-[#0d1117] p-5">
                <SH label="Hold Time Analysis" right={holdStats ? `${holdTimes.length} measured` : undefined} />
                {holdStats ? (
                  <div className="space-y-3">
                    {[
                      { label: 'Average Hold',  val: holdDuration(null, new Date(Date.now() - holdStats.avg).toISOString().replace(/^.*/, () => new Date(holdStats.avg).toISOString().slice(11, 19))), raw: holdStats.avg },
                      { label: 'Median Hold',   val: '', raw: holdStats.med },
                      { label: 'Shortest Hold', val: '', raw: holdStats.min },
                      { label: 'Longest Hold',  val: '', raw: holdStats.max },
                    ].map(r => {
                      const d = r.raw
                      const m = Math.round(d / 60000)
                      const h = Math.floor(m / 60)
                      const disp = m < 60 ? `${m}m` : h < 24 ? `${h}h ${m % 60}m` : `${Math.floor(h / 24)}d ${h % 24}h`
                      const pct = holdStats.max > 0 ? (r.raw / holdStats.max) * 100 : 0
                      return (
                        <div key={r.label}>
                          <div className="flex justify-between text-[10px] font-mono mb-1" style={{ color: isDark ? '#6b7280' : '#94a3b8' }}>
                            <span>{r.label}</span>
                            <span className="font-bold" style={{ color: isDark ? '#e2e8f0' : '#334155' }}>{disp}</span>
                          </div>
                          <div className="h-3 rounded overflow-hidden" style={{ background: isDark ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.05)' }}>
                            <div className="h-full rounded" style={{ width: `${pct}%`, background: 'rgba(59,130,246,0.6)' }} />
                          </div>
                        </div>
                      )
                    })}
                    <div className="grid grid-cols-2 gap-2 pt-2">
                      <div className="rounded bg-[#090d13] border border-white/[0.04] px-3 py-2">
                        <div className="text-[9px] uppercase tracking-wider text-gray-700">Avg Slippage</div>
                        <div className="font-mono font-bold text-sm text-yellow-500 mt-0.5">
                          {closed.length > 0 ? fmtPrice(arrMean(closed.map(t => t.slippage))) : '—'}
                        </div>
                      </div>
                      <div className="rounded bg-[#090d13] border border-white/[0.04] px-3 py-2">
                        <div className="text-[9px] uppercase tracking-wider text-gray-700">Total Slippage</div>
                        <div className="font-mono font-bold text-sm text-red-500 mt-0.5">
                          {closed.length > 0 ? `-${fmtCr(closed.reduce((s, t) => s + t.slippage, 0))}` : '—'}
                        </div>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="py-8 text-center text-gray-700 text-xs">
                    No entry_time data — hold times unavailable
                  </div>
                )}
              </div>
            </div>

            {/* ── P&L BY POD + STRATEGY ────────────────────────────────────── */}
            {(byPod.length > 0 || byStrategy.length > 0) && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {byPod.length > 0 && (
                  <div className="rounded-xl border border-white/[0.06] bg-[#0d1117] p-5">
                    <SH label="P&L by Pod / Desk" right={`${byPod.length} sources`} />
                    <HBarChart data={byPod} isDark={isDark} />
                  </div>
                )}
                {byStrategy.length > 0 && (
                  <div className="rounded-xl border border-white/[0.06] bg-[#0d1117] p-5">
                    <SH label="P&L by Strategy" right={`${byStrategy.length} strategies`} />
                    <HBarChart data={byStrategy} isDark={isDark} />
                  </div>
                )}
              </div>
            )}

            {/* ── STATISTICAL SIGNIFICANCE ─────────────────────────────────── */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div className="rounded-xl border border-white/[0.06] bg-[#0d1117] px-5 py-4">
                <div className="text-[10px] uppercase tracking-[0.15em] text-gray-700 font-semibold mb-1.5">T-Statistic</div>
                <div className={cn('font-mono font-bold text-2xl', tStatVal === null ? 'text-gray-700' : Math.abs(tStatVal) >= 2 ? 'text-green-400' : 'text-yellow-500')}>
                  {fmtStat(tStatVal)}
                </div>
                <div className="text-[10px] text-gray-700 mt-1">
                  {tStatVal !== null && Math.abs(tStatVal) >= 1.96 ? '≥ 1.96 — statistically significant at 95%' : '< 1.96 — not yet significant'}
                </div>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-[#0d1117] px-5 py-4">
                <div className="text-[10px] uppercase tracking-[0.15em] text-gray-700 font-semibold mb-1.5">P-Value</div>
                <div className={cn('font-mono font-bold text-2xl', pValVal === null ? 'text-gray-700' : pValVal < 0.05 ? 'text-green-400' : pValVal < 0.1 ? 'text-yellow-500' : 'text-red-400')}>
                  {pValVal !== null ? pValVal.toFixed(4) : '—'}
                </div>
                <div className="text-[10px] text-gray-700 mt-1">
                  {pValVal !== null ? (pValVal < 0.05 ? 'p < 0.05 — edge is real (two-tailed)' : pValVal < 0.1 ? 'p < 0.10 — marginal significance' : 'p ≥ 0.10 — could be chance') : 'Insufficient data'}
                </div>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-[#0d1117] px-5 py-4">
                <div className="text-[10px] uppercase tracking-[0.15em] text-gray-700 font-semibold mb-1.5">Sample Size</div>
                <div className={cn('font-mono font-bold text-2xl', closed.length >= 100 ? 'text-green-400' : closed.length >= 30 ? 'text-yellow-500' : 'text-red-400')}>
                  {closed.length}
                </div>
                <div className="text-[10px] text-gray-700 mt-1">
                  {closed.length >= 100 ? 'Good sample — metrics are reliable' : closed.length >= 30 ? 'Moderate — treat as directional' : 'Too small — do not over-interpret'}
                </div>
              </div>
            </div>

            {/* ── P&L BY SYMBOL TABLE ──────────────────────────────────────── */}
            {bySymbol.length > 0 && (
              <div className="rounded-xl border border-white/[0.06] bg-[#0d1117] overflow-hidden">
                <div className="px-5 pt-4 pb-3 border-b border-white/[0.04]">
                  <SH label={`P&L by Symbol — ${bySymbol.length} stocks`} right={`Turnover ${fmtCr(turnover)}`} />
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left">
                    <thead>
                      <tr className="text-[9px] uppercase tracking-[0.1em] text-gray-700 border-b border-white/[0.04]">
                        <th className="px-5 py-2.5">Symbol</th>
                        <th className="px-4 py-2.5 text-right">Trades</th>
                        <th className="px-4 py-2.5 text-right">Win%</th>
                        <th className="px-4 py-2.5 text-right">Turnover</th>
                        <th className="px-4 py-2.5 text-right">Avg/Trade</th>
                        <th className="px-4 py-2.5 text-right">Net P&L</th>
                        <th className="px-5 py-2.5 w-24" />
                      </tr>
                    </thead>
                    <tbody>
                      {bySymbol.map((r, i) => {
                        const wr = Math.round(r.wins / r.n * 100)
                        return (
                          <tr key={r.sym} className={cn('border-b border-white/[0.03] hover:bg-white/[0.02] transition-colors',
                            i === 0 && r.pnl > 0 ? 'bg-green-950/8' : '',
                            i === bySymbol.length - 1 && r.pnl < 0 ? 'bg-red-950/8' : '')}>
                            <td className="px-5 py-2.5 font-mono font-bold text-sm text-white">
                              {r.sym}
                              {i === 0 && r.pnl > 0 && <span className="ml-2 text-[9px] text-green-800">BEST</span>}
                              {i === bySymbol.length - 1 && r.pnl < 0 && <span className="ml-2 text-[9px] text-red-800">WORST</span>}
                            </td>
                            <td className="px-4 py-2.5 text-right font-mono text-sm text-gray-500">{r.n}</td>
                            <td className="px-4 py-2.5 text-right">
                              <span className={cn('font-mono font-semibold text-sm', wr >= 50 ? 'text-green-500' : 'text-red-500')}>{wr}%</span>
                            </td>
                            <td className="px-4 py-2.5 text-right font-mono text-xs text-gray-700">{fmtCr(r.tv)}</td>
                            <td className={cn('px-4 py-2.5 text-right font-mono text-xs', r.pnl / r.n >= 0 ? 'text-green-700' : 'text-red-700')}>
                              {r.pnl / r.n >= 0 ? '+' : ''}{fmtPrice(r.pnl / r.n)}
                            </td>
                            <td className={cn('px-4 py-2.5 text-right font-mono font-bold text-sm', r.pnl >= 0 ? 'text-green-400' : 'text-red-400')}>
                              {r.pnl >= 0 ? '+' : ''}{fmtPrice(r.pnl)}
                            </td>
                            <td className="px-5 py-2.5"><Bar val={r.pnl} max={maxSymPnl} /></td>
                          </tr>
                        )
                      })}
                    </tbody>
                    <tfoot>
                      <tr className="border-t border-white/[0.06] bg-[#090d13]">
                        <td className="px-5 py-3 text-[10px] font-bold uppercase tracking-wider text-gray-700">{bySymbol.length} symbols</td>
                        <td className="px-4 py-3 text-right font-mono text-sm text-gray-500">{closed.length}</td>
                        <td className="px-4 py-3 text-right">
                          <span className={cn('font-mono font-semibold text-sm', winRate >= 0.5 ? 'text-green-500' : 'text-red-500')}>{Math.round(winRate * 100)}%</span>
                        </td>
                        <td className="px-4 py-3 text-right font-mono text-xs text-gray-700">{fmtCr(turnover)}</td>
                        <td />
                        <td className={cn('px-4 py-3 text-right font-mono font-bold text-sm', totalPnl >= 0 ? 'text-green-400' : 'text-red-400')}>
                          {totalPnl >= 0 ? '+' : ''}{fmtPrice(totalPnl)}
                        </td>
                        <td />
                      </tr>
                    </tfoot>
                  </table>
                </div>
              </div>
            )}

            {/* ── TRADE LOG ─────────────────────────────────────────────────── */}
            <div className="rounded-xl border border-white/[0.06] bg-[#0d1117] overflow-hidden">
              <div className="px-5 pt-4 pb-3 border-b border-white/[0.04]">
                <SH label={`Trade Log — ${sortedClosed.length} closed`} right={`${allTrades.length} total orders`} />
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left">
                  <thead>
                    <tr className="text-[9px] uppercase tracking-[0.1em] text-gray-700 border-b border-white/[0.04]">
                      <th className="px-5 py-2.5">#</th>
                      <th className="px-4 py-2.5">Date / Time</th>
                      <th className="px-4 py-2.5">Symbol</th>
                      <th className="px-4 py-2.5 text-center">Side</th>
                      <th className="px-4 py-2.5 text-right">Qty</th>
                      <th className="px-4 py-2.5 text-right">Entry</th>
                      <th className="px-4 py-2.5 text-right">Exit</th>
                      <th className="px-4 py-2.5 text-right">Held</th>
                      <th className="px-4 py-2.5 text-right">P&L</th>
                      <th className="px-5 py-2.5">Source</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedClosed.map((t, i) => (
                      <tr key={t.trade_id ?? i} className="border-b border-white/[0.03] hover:bg-white/[0.02] transition-colors">
                        <td className="px-5 py-2 text-[10px] text-gray-700 font-mono">{i + 1}</td>
                        <td className="px-4 py-2 text-[11px] text-gray-500 font-mono whitespace-nowrap">{fmtDateTime(t.timestamp)}</td>
                        <td className="px-4 py-2 font-mono font-bold text-sm text-white">{t.symbol}</td>
                        <td className="px-4 py-2 text-center">
                          <span className={cn('text-[9px] font-bold uppercase px-1.5 py-0.5 rounded font-mono',
                            t.side === 'buy' ? 'bg-blue-950/60 text-blue-500' : 'bg-orange-950/60 text-orange-500')}>
                            {t.side}
                          </span>
                        </td>
                        <td className="px-4 py-2 text-right font-mono text-sm text-gray-400">{t.quantity}</td>
                        <td className="px-4 py-2 text-right font-mono text-xs text-gray-600">{fmtPrice(t.entry_price)}</td>
                        <td className="px-4 py-2 text-right font-mono text-xs text-gray-300">{fmtPrice(t.price)}</td>
                        <td className="px-4 py-2 text-right font-mono text-xs text-gray-500">{holdDuration(t.entry_time, t.timestamp)}</td>
                        <td className={cn('px-4 py-2 text-right font-mono font-bold text-sm', t.pnl >= 0 ? 'text-green-400' : 'text-red-400')}>
                          {t.pnl >= 0 ? '+' : ''}{fmtPrice(t.pnl)}
                        </td>
                        <td className="px-5 py-2 text-[10px] text-gray-700 max-w-[130px] truncate">
                          {(t.source_desk || t.source_pod || '—').replace(/_/g, ' ')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr className="border-t border-white/[0.07] bg-[#090d13]">
                      <td colSpan={8} className="px-5 py-3 text-[10px] font-bold uppercase tracking-wider text-gray-700">
                        {sortedClosed.length} trades · Turnover {fmtCr(turnover)}
                      </td>
                      <td className={cn('px-4 py-3 text-right font-mono font-bold text-sm', totalPnl >= 0 ? 'text-green-400' : 'text-red-400')}>
                        {totalPnl >= 0 ? '+' : ''}{fmtPrice(totalPnl)}
                      </td>
                      <td />
                    </tr>
                  </tfoot>
                </table>
              </div>
            </div>
          </>
        )}
      </div>
    </>
  )
}
