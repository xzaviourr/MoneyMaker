import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import { useLiveFeed } from './hooks/useLiveFeed'
import { ErrorBoundary } from './components/ErrorBoundary'
import Dashboard from './pages/Dashboard'
import PodsPage from './pages/Pods'
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
           isActive ? 'bg-brand-500 text-white' : 'text-gray-400 hover:text-white hover:bg-gray-800')
      }
    >
      {label}
    </NavLink>
  )
}

function StatusDot({ connected }: { connected: boolean }) {
  return (
    <span className={cn('inline-block w-2 h-2 rounded-full',
      connected ? 'bg-green-400 animate-pulse' : 'bg-red-500')} />
  )
}

export default function App() {
  const { connected } = useLiveFeed()

  return (
    <BrowserRouter>
      <div className="min-h-screen flex flex-col">
        {/* Top bar */}
        <header className="border-b border-gray-800 px-6 py-3 flex items-center gap-6">
          <span className="font-mono font-semibold text-brand-500 text-lg tracking-tight">
            MoneyMaker
          </span>
          <nav className="flex gap-1">
            <NavItem to="/"          label="Dashboard"         />
            <NavItem to="/flow"      label="Flow"               />
            <NavItem to="/portfolio" label="Portfolio Manager"  />
            <NavItem to="/pods"      label="Pods"               />
            <NavItem to="/trades"    label="Trades"             />
            <NavItem to="/reports"   label="Reports"            />
            <NavItem to="/decisions" label="Decisions"          />
            <NavItem to="/feedback"  label="Feedback"           />
            <NavItem to="/logs"      label="Logs"               />
          </nav>
          <div className="ml-auto flex items-center gap-2 text-xs text-gray-500">
            <StatusDot connected={connected} />
            {connected ? 'Live' : 'Reconnecting…'}
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 px-6 py-6">
          <ErrorBoundary>
            <Routes>
              <Route path="/"          element={<Dashboard />}            />
              <Route path="/portfolio" element={<PortfolioManagerPage />}  />
              <Route path="/positions" element={<PositionsPage />}         />
              <Route path="/flow"      element={<SystemFlow />}            />
              <Route path="/pods"      element={<PodsPage />}              />
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
