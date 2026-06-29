import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchJson, type ServiceLog } from '../lib/api'
import { cn } from '../lib/utils'

const TABS: { key: string; label: string }[] = [
  { key: 'yahoo_finance', label: 'Yahoo Finance' },
  { key: 'five_paisa',    label: '5Paisa' },
  { key: 'database',      label: 'Database' },
]

function levelColor(level: string) {
  if (level === 'error')   return 'bg-red-900 text-red-300'
  if (level === 'warning') return 'bg-yellow-900 text-yellow-300'
  return 'bg-gray-800 text-gray-300'
}

export default function LogsPage() {
  const [service, setService] = useState(TABS[0].key)
  const [openId, setOpenId]   = useState<number | null>(null)

  const { data: rows = [], isLoading } = useQuery<ServiceLog[]>({
    queryKey: ['logs', service],
    queryFn:  () => fetchJson(`/logs?service=${service}&limit=200`),
    refetchInterval: 5000,
  })

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Service Logs</h1>

      <div className="flex gap-1">
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => { setService(t.key); setOpenId(null) }}
            className={cn('px-3 py-1.5 rounded-lg text-sm font-medium transition-colors',
              service === t.key ? 'bg-brand-500 text-white' : 'text-gray-400 hover:text-white hover:bg-gray-800')}
          >
            {t.label}
          </button>
        ))}
      </div>

      {isLoading && <div className="text-gray-500 text-sm">Loading…</div>}

      <div className="space-y-1">
        {rows.map(r => (
          <div key={r.id} className="card">
            <button
              className="w-full flex items-center gap-3 text-left"
              onClick={() => setOpenId(openId === r.id ? null : r.id)}
            >
              <span className="text-xs font-mono text-gray-500">
                {new Date(r.ts * 1000).toLocaleTimeString()}
              </span>
              <span className={cn('badge', levelColor(r.level))}>{r.level}</span>
              <span className="text-sm text-gray-200">{r.message}</span>
              {r.details && <span className="ml-auto text-xs text-gray-600">click for details</span>}
            </button>
            {openId === r.id && r.details && (
              <pre className="mt-2 text-xs text-gray-400 bg-gray-950 rounded-lg p-3 overflow-x-auto">
                {JSON.stringify(r.details, null, 2)}
              </pre>
            )}
          </div>
        ))}
        {!isLoading && rows.length === 0 && (
          <div className="text-center text-gray-600 py-8">No logs recorded yet</div>
        )}
      </div>
    </div>
  )
}
