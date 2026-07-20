import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { LiveEvent } from './useLiveFeed'

const MAX_EVENTS = 500

export interface Portfolio {
  id:   string
  label: string
  port: number
}

const DEFAULT_PORTFOLIO: Portfolio = { id: 'default', label: 'Portfolio 1 (₹10L)', port: 8000 }

interface Store {
  liveEvents: LiveEvent[]
  addLiveEvent: (e: LiveEvent) => void
  clearEvents:  () => void

  selectedSymbol: string | null
  setSelectedSymbol: (s: string | null) => void

  portfolios: Portfolio[]
  selectedPortfolioId: string
  addPortfolio: (p: Portfolio) => void
  setSelectedPortfolioId: (id: string) => void
}

export const useStore = create<Store>()(
  persist(
    (set) => ({
      liveEvents: [],
      addLiveEvent: (e) => set(s => ({
        liveEvents: [e, ...s.liveEvents].slice(0, MAX_EVENTS)
      })),
      clearEvents: () => set({ liveEvents: [] }),

      selectedSymbol: null,
      setSelectedSymbol: (s) => set({ selectedSymbol: s }),

      portfolios: [DEFAULT_PORTFOLIO],
      selectedPortfolioId: DEFAULT_PORTFOLIO.id,
      addPortfolio: (p) => set(s => ({
        portfolios: s.portfolios.some(x => x.id === p.id) ? s.portfolios : [...s.portfolios, p]
      })),
      setSelectedPortfolioId: (id) => set({ selectedPortfolioId: id }),
    }),
    {
      name: 'mm-portfolios',
      // liveEvents/selectedSymbol are per-session, not worth persisting —
      // only the portfolio list and selection should survive a reload.
      partialize: (s) => ({ portfolios: s.portfolios, selectedPortfolioId: s.selectedPortfolioId }),
    }
  )
)
