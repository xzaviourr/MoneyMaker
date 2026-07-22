import { useState, useEffect, useRef } from 'react'
import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import { useLiveFeed } from './hooks/useLiveFeed'
import { useStore } from './hooks/useStore'
import { ErrorBoundary } from './components/ErrorBoundary'
import { DebateModal } from './components/DebateModal'
import { PortfolioSwitcher } from './components/PortfolioSwitcher'
import Dashboard from './pages/Dashboard'
import PodsPage from './pages/Pods'
import QueuePage from './pages/Queue'
import PositionsPage from './pages/Positions'
import PortfolioManagerPage from './pages/PortfolioManager'
import TradesPage from './pages/Trades'
import ReportsPage from './pages/Reports'
import DecisionsPage from './pages/Decisions'
import FeedbackPage from './pages/Feedback'
import SystemFlow from './pages/SystemFlow'
import LogsPage from './pages/Logs'
import { cn } from './lib/utils'

function NavItem({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn('px-3 py-1.5 rounded-lg text-sm font-medium transition-colors',
           isActive ? 'bg-brand-500 text-white' : 'text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white hover:bg-gray-100 dark:hover:bg-gray-800')
      }
    >
      {label}
    </NavLink>
  )
}

function ThemeToggle() {
  const [isDark, setIsDark] = useState(
    () => document.documentElement.classList.contains('dark')
  )

  useEffect(() => {
    if (isDark) {
      document.documentElement.classList.add('dark')
      localStorage.setItem('mm-theme', 'dark')
    } else {
      document.documentElement.classList.remove('dark')
      localStorage.setItem('mm-theme', 'light')
    }
  }, [isDark])

  return (
    <button
      onClick={() => setIsDark(v => !v)}
      className="w-8 h-8 flex items-center justify-center rounded-lg border border-gray-700 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white"
      title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
    >
      {isDark ? '☀' : '🌙'}
    </button>
  )
}

function StatusDot({ connected }: { connected: boolean }) {
  return (
    <span className={cn('inline-block w-2 h-2 rounded-full',
      connected ? 'bg-green-400 animate-pulse' : 'bg-red-500')} />
  )
}

function OfflineBanner({ connected }: { connected: boolean }) {
  const lastSeenRef = useRef<string | null>(null)
  useEffect(() => {
    if (connected) {
      lastSeenRef.current = new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
    }
  }, [connected])
  if (connected) return null
  const since = lastSeenRef.current ? `since ${lastSeenRef.current}` : 'not reachable'
  return (
    <div className="bg-yellow-950/80 border-b border-yellow-700/60 px-6 py-2 flex items-center gap-3 text-sm">
      <span className="text-yellow-400 font-bold shrink-0">⚠ Backend offline</span>
      <span className="text-yellow-200/70">
        Live data unavailable ({since}) — Decisions, Feedback and events will not update until backend restarts.
      </span>
      <code className="ml-auto shrink-0 text-[11px] bg-black/30 rounded px-2 py-0.5 text-yellow-300 font-mono whitespace-nowrap">
        python main.py --paper
      </code>
    </div>
  )
}

function SymbolFocus() {
  const selectedSymbol    = useStore(s => s.selectedSymbol)
  const setSelectedSymbol = useStore(s => s.setSelectedSymbol)
  const [showDebate, setShowDebate] = useState(false)

  if (!selectedSymbol) return null

  return (
    <>
      <div className="flex items-center gap-2 bg-brand-900/40 border border-brand-500/40 rounded-lg px-3 py-1.5">
        <span className="font-mono font-bold text-sm text-brand-400">{selectedSymbol}</span>
        <button
          onClick={() => setShowDebate(true)}
          className="text-xs font-semibold text-white bg-purple-700 hover:bg-purple-600 rounded px-2 py-0.5 transition-colors"
        >
          Debate ↗
        </button>
        <button
          onClick={() => setSelectedSymbol(null)}
          className="text-gray-400 hover:text-white text-lg leading-none font-light"
          aria-label="Clear selection"
        >
          ×
        </button>
      </div>
      {showDebate && (
        <DebateModal symbol={selectedSymbol} onClose={() => setShowDebate(false)} />
      )}
    </>
  )
}

export default function App() {
  const { connected } = useLiveFeed()

  return (
    <BrowserRouter>
      <div className="min-h-screen flex flex-col">
        {/* Top bar */}
        <header className="border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-950 px-6 py-3 flex items-center gap-6" role="banner">
          <span className="font-mono font-semibold text-brand-500 text-lg tracking-tight">
            MoneyMaker
          </span>
          <nav aria-label="Main navigation" className="flex gap-1">
            <NavItem to="/"          label="Dashboard"         />
            <NavItem to="/flow"      label="Flow"               />
            <NavItem to="/portfolio" label="Portfolio Manager"  />
            <NavItem to="/pods"      label="Pods"               />
            <NavItem to="/queue"     label="Queue"              />
            <NavItem to="/trades"    label="Trades"             />
            <NavItem to="/reports"   label="Reports"            />
            <NavItem to="/decisions" label="Decisions"          />
            <NavItem to="/feedback"  label="Feedback"           />
            <NavItem to="/logs"      label="Logs"               />
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <PortfolioSwitcher />
            <ThemeToggle />
            <SymbolFocus />
            <div
              className="flex items-center gap-2 text-xs text-gray-500"
              role="status"
              aria-live="polite"
              aria-label={connected ? 'Live connection active' : 'Reconnecting to server'}
            >
              <StatusDot connected={connected} />
              {connected ? 'Live' : 'Reconnecting…'}
            </div>
          </div>
        </header>

        <OfflineBanner connected={connected} />

        {/* Page content */}
        <main className="flex-1 px-6 py-6" role="main">
          <ErrorBoundary>
            <Routes>
              <Route path="/"          element={<Dashboard />}            />
              <Route path="/portfolio" element={<PortfolioManagerPage />}  />
              <Route path="/positions" element={<PositionsPage />}         />
              <Route path="/flow"      element={<SystemFlow />}            />
              <Route path="/pods"      element={<PodsPage />}              />
              <Route path="/queue"     element={<QueuePage />}             />
              <Route path="/trades"    element={<TradesPage />}            />
              <Route path="/reports"   element={<ReportsPage />}           />
              <Route path="/decisions" element={<DecisionsPage />}         />
              <Route path="/feedback"  element={<FeedbackPage />}          />
              <Route path="/logs"      element={<LogsPage />}              />
            </Routes>
          </ErrorBoundary>
        </main>
      </div>
    </BrowserRouter>
  )
}
