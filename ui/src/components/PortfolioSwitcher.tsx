import { useState, useRef, useEffect } from 'react'
import { useStore } from '../hooks/useStore'
import { cn } from '../lib/utils'

export function PortfolioSwitcher() {
  const portfolios = useStore(s => s.portfolios)
  const selectedPortfolioId = useStore(s => s.selectedPortfolioId)
  const setSelectedPortfolioId = useStore(s => s.setSelectedPortfolioId)
  const addPortfolio = useStore(s => s.addPortfolio)

  const [open, setOpen] = useState(false)
  const [adding, setAdding] = useState(false)
  const [label, setLabel] = useState('')
  const [port, setPort] = useState('')
  const rootRef = useRef<HTMLDivElement>(null)

  const current = portfolios.find(p => p.id === selectedPortfolioId) ?? portfolios[0]

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false)
        setAdding(false)
      }
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [])

  function submitAdd() {
    const portNum = parseInt(port, 10)
    if (!label.trim() || !portNum || portNum < 1 || portNum > 65535) return
    const id = `port-${portNum}`
    addPortfolio({ id, label: label.trim(), port: portNum })
    setSelectedPortfolioId(id)
    setLabel('')
    setPort('')
    setAdding(false)
    setOpen(false)
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-2 text-sm font-medium px-3 py-1.5 rounded-lg border border-gray-700 dark:border-gray-700 text-gray-300 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="w-1.5 h-1.5 rounded-full bg-brand-500 shrink-0" />
        {current?.label ?? 'Select portfolio'}
        <span className="text-xs text-gray-500">▾</span>
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-72 rounded-lg border border-gray-700 bg-white dark:bg-gray-900 shadow-lg z-50 overflow-hidden">
          <ul role="listbox" className="py-1">
            {portfolios.map(p => (
              <li key={p.id}>
                <button
                  onClick={() => { setSelectedPortfolioId(p.id); setOpen(false) }}
                  className={cn(
                    'w-full text-left px-4 py-2 text-sm flex items-center justify-between gap-2 transition-colors',
                    p.id === selectedPortfolioId
                      ? 'bg-brand-900/30 text-brand-400 font-semibold'
                      : 'text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800'
                  )}
                  role="option"
                  aria-selected={p.id === selectedPortfolioId}
                >
                  <span>{p.label}</span>
                  <span className="text-xs text-gray-500 font-mono">:{p.port}</span>
                </button>
              </li>
            ))}
          </ul>

          <div className="border-t border-gray-700">
            {adding ? (
              <div className="p-3 flex flex-col gap-2">
                <input
                  autoFocus
                  value={label}
                  onChange={e => setLabel(e.target.value)}
                  placeholder="Label, e.g. Portfolio 2 (₹5L)"
                  className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-brand-500"
                />
                <input
                  value={port}
                  onChange={e => setPort(e.target.value.replace(/\D/g, ''))}
                  placeholder="Backend port, e.g. 8001"
                  inputMode="numeric"
                  className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-white font-mono focus:outline-none focus:border-brand-500"
                />
                <p className="text-[11px] text-gray-500">
                  Assumes that backend is already running — this doesn't start it for you.
                </p>
                <div className="flex gap-2 justify-end">
                  <button
                    onClick={() => setAdding(false)}
                    className="text-xs text-gray-400 hover:text-white px-2 py-1"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={submitAdd}
                    className="text-xs font-semibold text-white bg-brand-600 hover:bg-brand-500 rounded px-2 py-1 transition-colors"
                  >
                    Add
                  </button>
                </div>
              </div>
            ) : (
              <button
                onClick={() => setAdding(true)}
                className="w-full text-left px-4 py-2 text-sm text-brand-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
              >
                + Add portfolio
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
