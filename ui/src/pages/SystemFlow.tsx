import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

function useDarkMode() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))
  useEffect(() => {
    const obs = new MutationObserver(() => setDark(document.documentElement.classList.contains('dark')))
    obs.observe(document.documentElement, { attributeFilter: ['class'] })
    return () => obs.disconnect()
  }, [])
  return dark
}
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  addEdge,
  useNodesState,
  useEdgesState,
  Position,
  Handle,
  type NodeProps,
  type Node,
  type Edge,
  type NodeChange,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchJson, postJson, type ServiceLog } from '../lib/api'
import { useStore } from '../hooks/useStore'

// Every non-pod node on the Flow page gets a pause/resume button — node id →
// toggle name. Pods have their own separate pause mechanism (real pod
// lifecycle pause, not a feature toggle) below, so they're not listed here.
//
// news/reddit/long_term_desk/yahoo_finance/regime_classifier/portfolio_guardian
// /feedback/news_extractor actually gate real background work when paused.
// five_paisa/database/data_sentinel/llm_gateway/capital_tracker/pod_supervisor
// /paper_broker are inert switches — those components have no loop to pause
// (they're either pure storage or only ever called synchronously by something
// else), so the button exists but flipping it doesn't change behaviour.
const TOGGLEABLE: Record<string, string> = {
  news:               'news',
  reddit:             'reddit',
  lt_desk_room1:      'long_term_desk',
  yahoo_finance:      'yahoo_finance',
  regime_classifier:  'regime_classifier',
  portfolio_guardian: 'portfolio_guardian',
  feedback_engine:    'feedback',
  news_extractor:     'news_extractor',
  five_paisa:         'five_paisa',
  database:           'database',
  data_sentinel:      'data_sentinel',
  llm_gateway:        'llm_gateway',
  capital_tracker:    'capital_tracker',
  pod_supervisor:     'pod_supervisor',
  paper_broker:       'paper_broker',
}

// Raw bus-message "source" strings → graph node ids, so a live WebSocket
// event can be traced to the exact node/edge it just passed through.
const SOURCE_TO_NODE: Record<string, string> = {
  news_watchdog:          'news',
  broker_gateway:         'paper_broker',
  'room1.committee_chair': 'lt_desk_room1',
  'lt_desk.room1':        'lt_desk_room1',
  portfolio_guardian:     'portfolio_guardian',
  regime_classifier:      'regime_classifier',
  capital_tracker:        'capital_tracker',
  pod_supervisor:         'pod_supervisor',
  data_sentinel:          'data_sentinel',
  feedback_engine:        'feedback_engine',
  yahoo_finance:          'yahoo_finance',
}
function nodeIdForSource(source: string): string {
  if (SOURCE_TO_NODE[source]) return SOURCE_TO_NODE[source]
  return `pod_${source}` // pod sources are raw pod ids, e.g. "event_pod"
}

function useRefLatest<T>(value: T) {
  const ref = useRef(value)
  useEffect(() => { ref.current = value }, [value])
  return ref
}

const CONTROLS_CSS = `
  .react-flow__controls-button {
    background: #0f172a !important;
    border-bottom: 1px solid #1e293b !important;
    fill: #94a3b8 !important;
  }
  .react-flow__controls-button:hover {
    background: #1e293b !important;
  }
  .react-flow__controls-button svg { fill: #94a3b8 !important; }
  @keyframes pulse-live {
    0%, 100% { box-shadow: 0 0 6px var(--pulse-color); }
    50%      { box-shadow: 0 0 14px var(--pulse-color), 0 0 4px var(--pulse-color); }
  }
`

// Parses the backend's "12s ago" / "3m ago" / "just now" / "never" strings back
// into seconds, so the UI can tell which nodes actually saw traffic recently.
function parseAgeSeconds(age: string): number | null {
  if (age === 'just now') return 0
  const s = age.match(/^(\d+)s ago$/)
  if (s) return parseInt(s[1], 10)
  const m = age.match(/^(\d+)m ago$/)
  if (m) return parseInt(m[1], 10) * 60
  const h = age.match(/^(\d+)h/)
  if (h) return parseInt(h[1], 10) * 3600
  return null // "never", "no events yet", "not yet", etc.
}

const LIVE_WINDOW_SECONDS = 90

// ── Types ─────────────────────────────────────────────────────────────────────

interface FlowInput  { from_node: string; label: string; value: string; age: string }
interface FlowOutput { to_node: string;   label: string; value: string; age: string }
interface FlowState  { title: string; lines: string[] }
interface ReasoningItem { symbol: string; decision: string; reasoning: string; ts: string; outcome: string }
interface SynthesisItem {
  symbol: string; sources: string[]; summary: string
  recommendation: string; rationale: string; ts: string
}
interface GistItem {
  symbol: string; source: string; headline: string
  stance: string; sentiment: string; rationale: string; ts: string
}

interface GraphNode extends Record<string, unknown> {
  id: string; label: string; type: string
  status: 'ok' | 'warn' | 'error'
  inputs:  FlowInput[]
  state:   FlowState
  outputs: FlowOutput[]
}

interface GraphEdge { id: string; source: string; target: string; label?: string }
interface RecentEvent { ts: number; type: string; source: string; summary: string }

interface GraphData {
  timestamp: string
  uptime_s: number
  nodes: GraphNode[]
  edges: GraphEdge[]
  recent_events: RecentEvent[]
}

// ── Status palette ─────────────────────────────────────────────────────────────

function getS(isDark: boolean) {
  return isDark
    ? {
        ok:    { dot: '#22c55e', border: '#166534', bg: '#020f06' },
        warn:  { dot: '#eab308', border: '#713f12', bg: '#0d0800' },
        error: { dot: '#ef4444', border: '#7f1d1d', bg: '#0d0000' },
      }
    : {
        ok:    { dot: '#16a34a', border: '#bbf7d0', bg: '#f0fdf4' },
        warn:  { dot: '#ca8a04', border: '#fde68a', bg: '#fefce8' },
        error: { dot: '#dc2626', border: '#fecaca', bg: '#fff5f5' },
      }
}

const ICON: Record<string, string> = {
  source: '🌐', processor: '⚙️', orchestrator: '🎯', pod: '📦', sink: '💰',
}

// ── Node card ─────────────────────────────────────────────────────────────────

function SystemNode({ data }: NodeProps) {
  const isDark = useDarkMode()
  const S      = getS(isDark)
  const d      = data as unknown as GraphNode
  const pal    = S[d.status as keyof typeof S] ?? S.warn
  const isPod  = d.type === 'pod'

  // "Active" here means this node actually saw traffic recently — distinct
  // from health status (ok/warn/error), which can be green even if idle.
  const ages = [
    ...((d.inputs as FlowInput[] | undefined)?.map(i => i.age) ?? []),
    ...((d.outputs as FlowOutput[] | undefined)?.map(o => o.age) ?? []),
  ]
  const recentSecs = ages.map(parseAgeSeconds).filter((s): s is number => s !== null)
  const isLive = recentSecs.length > 0 && Math.min(...recentSecs) < LIVE_WINDOW_SECONDS

  const sectionStyle: React.CSSProperties = {
    borderTop: `1px solid ${isDark ? '#1e293b' : '#e2e8f0'}`,
    paddingTop: 5,
    marginTop: 5,
  }
  const labelStyle: React.CSSProperties = {
    fontSize: 8,
    textTransform: 'uppercase',
    letterSpacing: '0.08em',
    color: isDark ? '#475569' : '#94a3b8',
    marginBottom: 3,
  }
  const rowStyle: React.CSSProperties = {
    display: 'flex',
    gap: 4,
    alignItems: 'baseline',
    marginBottom: 2,
    fontSize: 10,
    lineHeight: 1.4,
  }

  return (
    <div style={{
      background: pal.bg,
      border: `1.5px solid ${pal.border}`,
      borderRadius: isPod ? 8 : 10,
      padding: '8px 10px',
      minWidth: 200,
      maxWidth: 260,
      fontFamily: 'monospace',
      color: isDark ? '#cbd5e1' : '#1e293b',
      boxShadow: `0 0 14px ${pal.dot}18`,
      opacity: isLive ? 1 : 0.55,
      transition: 'opacity 0.4s',
    }}>
      <Handle type="target" position={Position.Top}    style={{ background: '#334155', width: 8, height: 8 }} />
      <Handle type="source" position={Position.Bottom} style={{ background: '#334155', width: 8, height: 8 }} />

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
        <span style={{ fontSize: 14 }}>{ICON[d.type as string] ?? '●'}</span>
        <span style={{ fontWeight: 700, color: isDark ? '#f1f5f9' : '#0f172a', fontSize: 12, flex: 1 }}>{String(d.label)}</span>
        {isLive && (
          <span style={{ fontSize: 8, color: pal.dot, fontWeight: 700, letterSpacing: '0.05em' }}>LIVE</span>
        )}
        <span style={{
          width: 8, height: 8, borderRadius: '50%',
          background: pal.dot, flexShrink: 0,
          boxShadow: isLive ? `0 0 6px ${pal.dot}` : `0 0 3px ${pal.dot}55`,
          animation: isLive ? 'pulse-live 1.4s ease-in-out infinite' : undefined,
          ...({ '--pulse-color': pal.dot } as React.CSSProperties),
        }} />
      </div>

      {/* INPUTS */}
      {d.inputs && d.inputs.length > 0 && (
        <div style={sectionStyle}>
          <div style={labelStyle}>↓ IN</div>
          {(d.inputs as FlowInput[]).map((inp, i) => (
            <div key={i} style={rowStyle}>
              <span style={{ color: '#3b82f6', fontSize: 9 }}>←</span>
              <div>
                <span style={{ color: isDark ? '#64748b' : '#475569' }}>{inp.label}</span>
                {inp.value && <span style={{ color: isDark ? '#94a3b8' : '#64748b' }}> · {inp.value}</span>}
                <span style={{ color: isDark ? '#334155' : '#94a3b8', fontSize: 9 }}> ({inp.age})</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* STATE */}
      <div style={sectionStyle}>
        <div style={labelStyle}>◈ NOW</div>
        <div style={{ color: isDark ? '#64748b' : '#475569', fontWeight: 600, fontSize: 10, marginBottom: 3 }}>
          {(d.state as FlowState).title}
        </div>
        {(d.state as FlowState).lines.slice(0, isPod ? 4 : 5).map((line, i) => (
          <div key={i} style={{ fontSize: 9.5, color: isDark ? '#475569' : '#374155', lineHeight: 1.5 }}>{line}</div>
        ))}
      </div>

      {/* OUTPUTS */}
      {d.outputs && d.outputs.length > 0 && (
        <div style={sectionStyle}>
          <div style={labelStyle}>↑ OUT</div>
          {(d.outputs as FlowOutput[]).map((out, i) => (
            <div key={i} style={rowStyle}>
              <span style={{ color: '#22c55e', fontSize: 9 }}>→</span>
              <div>
                <span style={{ color: isDark ? '#64748b' : '#475569' }}>{out.label}</span>
                {out.value && <span style={{ color: isDark ? '#94a3b8' : '#64748b' }}> · {out.value}</span>}
                <span style={{ color: isDark ? '#334155' : '#94a3b8', fontSize: 9 }}> ({out.age})</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const nodeTypes = { system: SystemNode }

const LOG_SERVICES = new Set(['yahoo_finance', 'five_paisa', 'news', 'reddit', 'database'])

function levelColor(level: string) {
  if (level === 'error')   return '#ef4444'
  if (level === 'warning') return '#eab308'
  return '#64748b'
}

// ── Layout ─────────────────────────────────────────────────────────────────────

const LAYOUT: Record<string, { x: number; y: number }> = {
  // Row 0 — data sources, generously spaced (260px-wide cards need >=320px gaps).
  // News goes first — it's the entry point for the news → event-pod trade pipeline.
  news:                 { x: 0,    y: 0    },
  reddit:               { x: 0,    y: 320  },
  yahoo_finance:        { x: 380,  y: 0    },
  five_paisa:           { x: 760,  y: 0    },
  database:             { x: 1140, y: 0    },
  // Row 1
  data_sentinel:        { x: 380,  y: 320  },
  news_extractor:       { x: 760,  y: 320  },
  // Row 2
  regime_classifier:    { x: 40,   y: 620  },
  pod_supervisor:       { x: 380,  y: 620  },
  llm_gateway:          { x: 760,  y: 620  },
  // Row 3 — pods (positioned dynamically below, same y)
  // Row 4
  capital_tracker:      { x: 380,  y: 1140 },
  portfolio_guardian:   { x: 760,  y: 1140 },
  lt_desk_room1:        { x: 1140, y: 1140 },
  // Row 5
  paper_broker:         { x: 380,  y: 1380 },
  feedback_engine:      { x: 760,  y: 1380 },
}

// Manually dragged positions, saved per-browser so they survive the 3s graph
// refetch (which used to rebuild every node from the fixed LAYOUT and silently
// snap any drag back) and page reloads. Not synced anywhere — just this machine.
const CUSTOM_POS_KEY = 'moneymaker_flow_node_positions'

function loadCustomPositions(): Record<string, { x: number; y: number }> {
  try {
    return JSON.parse(localStorage.getItem(CUSTOM_POS_KEY) ?? '{}')
  } catch {
    return {}
  }
}

function saveCustomPosition(id: string, position: { x: number; y: number }): void {
  const all = loadCustomPositions()
  all[id] = position
  localStorage.setItem(CUSTOM_POS_KEY, JSON.stringify(all))
}

function buildFlowNodes(apiNodes: GraphNode[]): Node[] {
  const pods     = apiNodes.filter(n => n.type === 'pod')
  const podStart = 380 - ((pods.length - 1) * 320) / 2

  const podPos: Record<string, { x: number; y: number }> = {}
  pods.forEach((p, i) => { podPos[p.id] = { x: podStart + i * 320, y: 900 } })

  const custom = loadCustomPositions()

  return apiNodes.map(n => ({
    id:       n.id,
    type:     'system',
    data:     n,
    position: custom[n.id] ?? LAYOUT[n.id] ?? podPos[n.id] ?? { x: 320, y: 580 },
    draggable: true,
  }))
}

function buildFlowEdges(apiEdges: GraphEdge[], isDark: boolean): Edge[] {
  return apiEdges.map(e => ({
    id:           e.id,
    source:       e.source,
    target:       e.target,
    label:        e.label,
    animated:     true,
    style:        { stroke: isDark ? '#1e3a5f' : '#94a3b8', strokeWidth: 1.5 },
    labelStyle:   { fill: isDark ? '#475569' : '#374155', fontSize: 9, fontFamily: 'monospace' },
    labelBgStyle: { fill: isDark ? '#020817' : '#f8fafc', fillOpacity: 0.9 },
    type:         'smoothstep',
  }))
}

function fmtUptime(s: number) {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60
  return h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m ${sec}s` : `${sec}s`
}

// ── Exported component (can be embedded or used standalone) ───────────────────

interface Props { height?: string }

export default function SystemFlow({ height = 'calc(100vh - 100px)' }: Props) {
  const isDark = useDarkMode()
  const S      = getS(isDark)
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])

  // Persist a node's new position the moment a drag finishes, so the next
  // /system/graph refetch (every 3s) doesn't snap it back to the fixed layout.
  const onNodesChangePersisted = useCallback((changes: NodeChange[]) => {
    onNodesChange(changes)
    for (const c of changes) {
      if (c.type === 'position' && c.position && c.dragging === false) {
        saveCustomPosition(c.id, c.position)
      }
    }
  }, [onNodesChange])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [selected, setSelected] = useState<GraphNode | null>(null)
  const [openLogId, setOpenLogId] = useState<number | null>(null)
  const [pulseEdges, setPulseEdges] = useState<Set<string>>(new Set())
  const [pulseNodes, setPulseNodes] = useState<Set<string>>(new Set())

  // App.tsx already keeps one WebSocket connection open for the whole app —
  // a second useLiveFeed() call here used to open a duplicate connection,
  // processing every event twice and adding extra load to this already-heavy page.
  const latestEvent = useStore(s => s.liveEvents[0])

  const { data: graph } = useQuery<GraphData>({
    queryKey:        ['system-graph'],
    queryFn:         () => fetchJson('/system/graph'),
    refetchInterval: 3000,
  })

  const qc = useQueryClient()
  const { data: toggles = {} } = useQuery<Record<string, boolean>>({
    queryKey:        ['toggles'],
    queryFn:         () => fetchJson('/system/toggles'),
    refetchInterval: 3000,
  })
  const toggleMutation = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      postJson(`/system/toggles/${name}`, { enabled }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['toggles'] }); qc.invalidateQueries({ queryKey: ['system-graph'] }) },
  })
  const podCommandMutation = useMutation({
    mutationFn: ({ podId, action }: { podId: string; action: 'pause' | 'resume' }) =>
      postJson(`/pods/${podId}/command`, { action }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['system-graph'] }),
  })

  const showLogs = !!selected && LOG_SERVICES.has(selected.id)
  const { data: nodeLogs = [] } = useQuery<ServiceLog[]>({
    queryKey:        ['node-logs', selected?.id],
    queryFn:         () => fetchJson(`/logs?service=${selected?.id}&limit=50`),
    enabled:         showLogs,
    refetchInterval: 5000,
  })

  useEffect(() => {
    if (!graph) return
    setNodes(buildFlowNodes(graph.nodes))
    setEdges(buildFlowEdges(graph.edges, isDark))
  }, [graph, setNodes, setEdges])

  // On every real event from the bus, light up the exact edge(s) it just
  // travelled along — this is the "show me it flowing" piece: a visible,
  // timed pulse tied to an actual thing that just happened, not decoration.
  // Reads the graph via ref (not as a dependency) — /system/graph refetches
  // every 3s, and depending on it directly would re-fire this on every poll
  // for the same stale event instead of once per real new event.
  const graphRef = useRefLatest(graph)
  useEffect(() => {
    if (!latestEvent) return
    if (['heartbeat', 'pong', 'connected'].includes(latestEvent.type)) return
    const g = graphRef.current
    if (!g) return

    const fromNode = nodeIdForSource(latestEvent.source)
    const matched  = g.edges.filter(e => e.source === fromNode)
    if (matched.length === 0) return

    setPulseEdges(new Set(matched.map(e => e.id)))
    setPulseNodes(new Set([fromNode, ...matched.map(e => e.target)]))

    const t = setTimeout(() => {
      setPulseEdges(new Set())
      setPulseNodes(new Set())
    }, 1800)
    return () => clearTimeout(t)
  }, [latestEvent])

  const displayEdges = useMemo(() => edges.map(e =>
    pulseEdges.has(e.id)
      ? { ...e, style: { stroke: '#22c55e', strokeWidth: 3.5 }, zIndex: 10 }
      : e
  ), [edges, pulseEdges])

  const displayNodes = useMemo(() => nodes.map(n =>
    pulseNodes.has(n.id)
      ? { ...n, style: { ...(n.style ?? {}), boxShadow: '0 0 0 3px #22c55e, 0 0 30px #22c55e' }, zIndex: 10 }
      : n
  ), [nodes, pulseNodes])

  const onConnect = useCallback(
    (p: Parameters<typeof addEdge>[0]) => setEdges(eds => addEdge(p, eds)),
    [setEdges]
  )
  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    setSelected(node.data as unknown as GraphNode)
    setOpenLogId(null)
  }, [])

  const okCount   = graph?.nodes.filter(n => n.status === 'ok').length   ?? 0
  const warnCount = graph?.nodes.filter(n => n.status === 'warn').length  ?? 0
  const errCount  = graph?.nodes.filter(n => n.status === 'error').length ?? 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height, gap: 10 }}>
      {/* Toolbar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, fontSize: 12, fontFamily: 'monospace', flexShrink: 0 }}>
        <span style={{ color: '#64748b' }}>
          {graph ? `updated ${new Date(graph.timestamp).toLocaleTimeString()}` : 'loading…'}
        </span>
        {graph && <span style={{ color: '#64748b' }}>uptime {fmtUptime(graph.uptime_s)}</span>}
        <span style={{ color: '#22c55e' }}>● {okCount} ok</span>
        {warnCount > 0 && <span style={{ color: '#eab308' }}>● {warnCount} warn</span>}
        {errCount  > 0 && <span style={{ color: '#ef4444' }}>● {errCount} error</span>}
        <span style={{ marginLeft: 'auto', color: '#334155', fontSize: 11 }}>
          Click a node to inspect its data flow →
        </span>
      </div>

      {/* Canvas + side panel */}
      <div style={{ display: 'flex', flex: 1, gap: 10, minHeight: 0 }}>

        {/* React Flow */}
        <div style={{ flex: 1, borderRadius: 10, overflow: 'hidden', border: `1px solid ${isDark ? '#1e293b' : '#e2e8f0'}` }}>
          <ReactFlow
            nodes={displayNodes} edges={displayEdges}
            onNodesChange={onNodesChangePersisted} onEdgesChange={onEdgesChange}
            onConnect={onConnect} onNodeClick={onNodeClick}
            nodeTypes={nodeTypes}
            defaultViewport={{ x: 40, y: 40, zoom: 0.7 }}
            minZoom={0.3} maxZoom={1.5}
            panOnScroll panOnScrollSpeed={0.8}
            zoomOnScroll={false} zoomOnPinch
            style={{ background: isDark ? '#020817' : '#f8fafc' }}
            proOptions={{ hideAttribution: true }}
          >
            <Background color={isDark ? '#0f172a' : '#e2e8f0'} gap={24} size={1} />
            <style>{CONTROLS_CSS}</style>
            <Controls style={{ background: isDark ? '#0f172a' : '#ffffff', border: `1px solid ${isDark ? '#1e293b' : '#e2e8f0'}` }} />
            <MiniMap
              style={{ background: isDark ? '#0f172a' : '#ffffff', border: `1px solid ${isDark ? '#1e293b' : '#e2e8f0'}`, height: 90 }}
              nodeColor={(n: Node) => S[(n.data as unknown as GraphNode)?.status as keyof typeof S]?.dot ?? '#475569'}
              maskColor={isDark ? 'rgba(2,8,23,0.75)' : 'rgba(248,250,252,0.75)'}
            />
          </ReactFlow>
        </div>

        {/* Detail panel */}
        <div style={{
          width: 280, background: isDark ? '#0a0f1a' : '#ffffff', border: `1px solid ${isDark ? '#1e293b' : '#e2e8f0'}`,
          borderRadius: 10, padding: 14, fontSize: 11, fontFamily: 'monospace',
          color: isDark ? '#64748b' : '#334155', overflowY: 'auto', flexShrink: 0,
          display: 'flex', flexDirection: 'column', gap: 12,
        }}>
          {selected ? (
            <>
              {/* Node title */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{
                  width: 10, height: 10, borderRadius: '50%', flexShrink: 0,
                  background: S[selected.status as keyof typeof S]?.dot,
                  boxShadow: `0 0 8px ${S[selected.status as keyof typeof S]?.dot}`,
                }} />
                <span style={{ fontWeight: 700, color: isDark ? '#f1f5f9' : '#0f172a', fontSize: 13 }}>{String(selected.label)}</span>
              </div>

              {/* Long-Term Desk reasoning — its own highlighted block, not
                  buried inside the generic state-lines list, since that was
                  apparently too easy to miss. This is the buy/no-buy "why". */}
              {selected.id === 'lt_desk_room1' && (() => {
                const feed = (selected.reasoning_feed as ReasoningItem[] | undefined) ?? []
                return (
                  <div style={{
                    border: `1px solid ${isDark ? '#1e40af' : '#bfdbfe'}`, borderRadius: 8, padding: 10,
                    background: isDark ? '#0a1230' : '#eff6ff', display: 'flex', flexDirection: 'column', gap: 8,
                  }}>
                    <div style={{ fontSize: 10, fontWeight: 700, color: isDark ? '#60a5fa' : '#1d4ed8', letterSpacing: '0.05em' }}>
                      💬 WHY IT BUYS / REJECTS — latest debate verdicts
                    </div>
                    {feed.length === 0 && (
                      <div style={{ color: '#475569', fontSize: 10 }}>No ideas debated yet.</div>
                    )}
                    {feed.map((item, i) => {
                      const rejected = item.decision === 'reject'
                      return (
                        <div key={i} style={{
                          borderLeft: `3px solid ${rejected ? '#7f1d1d' : '#166534'}`,
                          paddingLeft: 8,
                        }}>
                          <div style={{ display: 'flex', gap: 6, alignItems: 'baseline', marginBottom: 2 }}>
                            <span style={{ fontWeight: 700, color: isDark ? '#e2e8f0' : '#0f172a', fontSize: 11 }}>{item.symbol}</span>
                            <span style={{
                              fontSize: 9, fontWeight: 700, textTransform: 'uppercase',
                              color: rejected ? '#ef4444' : '#22c55e',
                            }}>
                              {item.decision}
                            </span>
                          </div>
                          <div style={{ color: isDark ? '#94a3b8' : '#475569', fontSize: 10, lineHeight: 1.5 }}>
                            {item.reasoning}
                          </div>
                          {/* The action that actually resulted — bought or not,
                              how much, at what target/stop — not just the verdict. */}
                          <div style={{
                            marginTop: 4, fontSize: 10, fontWeight: 600,
                            color: item.outcome.startsWith('BOUGHT') ? '#22c55e'
                                 : item.outcome.startsWith('pending') ? '#64748b' : '#eab308',
                          }}>
                            {item.outcome.startsWith('BOUGHT') ? '✓ ' : item.outcome.startsWith('pending') ? '⏳ ' : '✗ '}
                            {item.outcome}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )
              })()}

              {/* News Extractor — gist feed first (primary, always populated:
                  every stock-specific headline from any ONE source gets its own
                  buy/avoid/watch call), cross-source matches second (a bonus
                  highlight for when 2+ sources happen to agree on the same stock). */}
              {selected.id === 'news_extractor' && (() => {
                const gists = (selected.gist_feed as GistItem[] | undefined) ?? []
                const matches = (selected.synthesis_feed as SynthesisItem[] | undefined) ?? []
                const stanceColor = (s: string) =>
                  s === 'buy' ? '#22c55e' : s === 'avoid' ? '#ef4444' : '#eab308'
                return (
                  <>
                    <div style={{
                      border: `1px solid ${isDark ? '#1e40af' : '#bfdbfe'}`, borderRadius: 8, padding: 10,
                      background: isDark ? '#0a1230' : '#eff6ff', display: 'flex', flexDirection: 'column', gap: 8,
                    }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: isDark ? '#60a5fa' : '#1d4ed8', letterSpacing: '0.05em' }}>
                        📰 NEWS GISTS — buy / avoid / watch, per headline
                      </div>
                      {gists.length === 0 && (
                        <div style={{ color: '#475569', fontSize: 10 }}>No headlines processed yet.</div>
                      )}
                      {gists.slice(0, 12).map((item, i) => (
                        <div key={i} style={{ borderLeft: `3px solid ${stanceColor(item.stance)}`, paddingLeft: 8 }}>
                          <div style={{ display: 'flex', gap: 6, alignItems: 'baseline', marginBottom: 2 }}>
                            <span style={{ fontWeight: 700, color: '#e2e8f0', fontSize: 11 }}>{item.symbol}</span>
                            <span style={{ fontSize: 9, color: '#64748b' }}>{item.source}</span>
                            <span style={{
                              fontSize: 9, fontWeight: 700, textTransform: 'uppercase',
                              color: stanceColor(item.stance),
                            }}>
                              {item.stance}
                            </span>
                          </div>
                          <div style={{ color: '#94a3b8', fontSize: 10, lineHeight: 1.5 }}>{item.headline}</div>
                          <div style={{ color: '#64748b', fontSize: 10, lineHeight: 1.5, marginTop: 2 }}>{item.rationale}</div>
                        </div>
                      ))}
                    </div>

                    <div style={{
                      border: `1px solid ${isDark ? '#1e293b' : '#e2e8f0'}`, borderRadius: 8, padding: 10,
                      background: isDark ? '#0a0f1a' : '#f8fafc', display: 'flex', flexDirection: 'column', gap: 8,
                    }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: isDark ? '#94a3b8' : '#64748b', letterSpacing: '0.05em' }}>
                        🔎 CROSS-SOURCE MATCHES — 2+ sources, same story
                      </div>
                      {matches.length === 0 && (
                        <div style={{ color: '#475569', fontSize: 10 }}>No overlapping stories found yet.</div>
                      )}
                      {matches.map((item, i) => (
                        <div key={i} style={{ borderLeft: '3px solid #1e40af', paddingLeft: 8 }}>
                          <div style={{ display: 'flex', gap: 6, alignItems: 'baseline', marginBottom: 2 }}>
                            <span style={{ fontWeight: 700, color: '#e2e8f0', fontSize: 11 }}>{item.symbol}</span>
                            <span style={{ fontSize: 9, color: '#64748b' }}>{item.sources.join(' + ')}</span>
                            <span style={{
                              fontSize: 9, fontWeight: 700, textTransform: 'uppercase',
                              color: stanceColor(item.recommendation),
                            }}>
                              {item.recommendation}
                            </span>
                          </div>
                          <div style={{ color: '#94a3b8', fontSize: 10, lineHeight: 1.5 }}>{item.summary}</div>
                          <div style={{ color: '#64748b', fontSize: 10, lineHeight: 1.5, marginTop: 2 }}>{item.rationale}</div>
                        </div>
                      ))}
                    </div>
                  </>
                )
              })()}

              {/* Pause/resume — only for feeds it's safe to pause (never core quotes) */}
              {TOGGLEABLE[selected.id] && (() => {
                const toggleName = TOGGLEABLE[selected.id]
                const enabled = toggles[toggleName] !== false
                return (
                  <button
                    onClick={() => toggleMutation.mutate({ name: toggleName, enabled: !enabled })}
                    disabled={toggleMutation.isPending}
                    style={{
                      padding: '6px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600,
                      cursor: 'pointer', border: '1px solid',
                      borderColor: enabled ? '#7f1d1d' : '#166534',
                      background: enabled ? '#1a0a0a' : '#06150a',
                      color: enabled ? '#ef4444' : '#22c55e',
                    }}
                  >
                    {enabled ? '⏸ Pause this feed' : '▶ Resume this feed'}
                  </button>
                )
              })()}

              {/* Pause/resume — pods only. Same lifecycle command as the Pods
                  page, just surfaced here too. Never offered for core plumbing
                  (broker, capital tracker, guardian, quotes) — pausing those
                  isn't a "feed", it's the trading loop itself. */}
              {selected.type === 'pod' && typeof selected.pod_id === 'string' && (() => {
                const podId = selected.pod_id as string
                const isPaused = !!selected.is_paused
                return (
                  <button
                    onClick={() => podCommandMutation.mutate({ podId, action: isPaused ? 'resume' : 'pause' })}
                    disabled={podCommandMutation.isPending}
                    style={{
                      padding: '6px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600,
                      cursor: 'pointer', border: '1px solid',
                      borderColor: isPaused ? '#166534' : '#7f1d1d',
                      background: isPaused ? '#06150a' : '#1a0a0a',
                      color: isPaused ? '#22c55e' : '#ef4444',
                    }}
                  >
                    {isPaused ? '▶ Resume this pod' : '⏸ Pause this pod'}
                  </button>
                )
              })()}

              {/* Service logs (Yahoo Finance / 5Paisa only) */}
              {showLogs && (
                <Section title="🗒 CALL LOG — click a row for details">
                  {nodeLogs.length === 0 && (
                    <div style={{ color: '#1e293b' }}>No calls logged yet</div>
                  )}
                  {nodeLogs.map(row => (
                    <div key={row.id} style={{ marginBottom: 6 }}>
                      <div
                        onClick={() => setOpenLogId(openLogId === row.id ? null : row.id)}
                        style={{
                          cursor: 'pointer', display: 'flex', gap: 6,
                          alignItems: 'baseline', color: levelColor(row.level),
                        }}
                      >
                        <span style={{ color: '#1e293b', fontSize: 9 }}>
                          {new Date(row.ts * 1000).toLocaleTimeString()}
                        </span>
                        <span style={{ fontSize: 10 }}>{row.message}</span>
                      </div>
                      {openLogId === row.id && row.details && (
                        <pre style={{
                          fontSize: 9, color: isDark ? '#475569' : '#334155', background: isDark ? '#020817' : '#f1f5f9',
                          borderRadius: 6, padding: 6, marginTop: 4, overflowX: 'auto',
                        }}>
                          {JSON.stringify(row.details, null, 2)}
                        </pre>
                      )}
                    </div>
                  ))}
                </Section>
              )}

              {/* Inputs */}
              {(selected.inputs as FlowInput[]).length > 0 && (
                <Section title="↓ DATA IN">
                  {(selected.inputs as FlowInput[]).map((inp, i) => (
                    <Item key={i}
                      left={<><span style={{ color: '#3b82f6' }}>←</span> {inp.from_node}</>}
                      right={inp.age}
                    >
                      <div style={{ color: '#94a3b8', marginTop: 2 }}>{inp.label}</div>
                      {inp.value && <div style={{ color: '#64748b', fontSize: 10 }}>{inp.value}</div>}
                    </Item>
                  ))}
                </Section>
              )}

              {/* State */}
              <Section title="◈ CURRENT STATE">
                <div style={{ color: '#94a3b8', fontWeight: 600, marginBottom: 4 }}>
                  {(selected.state as FlowState).title}
                </div>
                {(selected.state as FlowState).lines.map((l, i) => (
                  <div key={i} style={{ color: '#475569', lineHeight: 1.7 }}>{l}</div>
                ))}
              </Section>

              {/* Outputs */}
              {(selected.outputs as FlowOutput[]).length > 0 && (
                <Section title="↑ DATA OUT">
                  {(selected.outputs as FlowOutput[]).map((out, i) => (
                    <Item key={i}
                      left={<><span style={{ color: '#22c55e' }}>→</span> {out.to_node}</>}
                      right={out.age}
                    >
                      <div style={{ color: '#94a3b8', marginTop: 2 }}>{out.label}</div>
                      {out.value && <div style={{ color: '#64748b', fontSize: 10 }}>{out.value}</div>}
                    </Item>
                  ))}
                </Section>
              )}
            </>
          ) : (
            <>
              {/* Recent bus events when nothing selected */}
              <div style={{ color: '#475569', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                Live message bus
              </div>
              {(graph?.recent_events ?? []).slice().reverse().slice(0, 20).map((e, i) => (
                <div key={i} style={{
                  borderBottom: '1px solid #0f172a', paddingBottom: 5,
                  display: 'flex', flexDirection: 'column', gap: 1,
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ color: '#3b82f6', fontSize: 10 }}>{e.type}</span>
                    <span style={{ color: '#1e293b', fontSize: 9 }}>
                      {new Date(e.ts * 1000).toLocaleTimeString()}
                    </span>
                  </div>
                  <div style={{ color: '#334155', fontSize: 10 }}>from: {e.source}</div>
                  {e.summary && <div style={{ color: '#475569', fontSize: 10 }}>{e.summary}</div>}
                </div>
              ))}
              {(graph?.recent_events ?? []).length === 0 && (
                <div style={{ color: '#1e293b', marginTop: 20, textAlign: 'center', lineHeight: 2 }}>
                  Waiting for<br />system events…<br /><br />
                  <span style={{ color: '#1e293b' }}>Click any node<br />to inspect it</span>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Panel helpers ─────────────────────────────────────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ borderTop: '1px solid #1e293b', paddingTop: 10 }}>
      <div style={{ fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#334155', marginBottom: 6 }}>
        {title}
      </div>
      {children}
    </div>
  )
}

function Item({ left, right, children }: { left: React.ReactNode; right: string; children?: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', color: '#64748b' }}>
        <span>{left}</span>
        <span style={{ color: '#1e293b', fontSize: 10 }}>{right}</span>
      </div>
      {children}
    </div>
  )
}
