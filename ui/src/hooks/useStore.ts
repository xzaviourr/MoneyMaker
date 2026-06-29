import { create } from 'zustand'
import type { LiveEvent } from './useLiveFeed'

const MAX_EVENTS = 500

interface Store {
  liveEvents: LiveEvent[]
  addLiveEvent: (e: LiveEvent) => void
  clearEvents:  () => void
}

export const useStore = create<Store>((set) => ({
  liveEvents: [],
  addLiveEvent: (e) => set(s => ({
    liveEvents: [e, ...s.liveEvents].slice(0, MAX_EVENTS)
  })),
  clearEvents: () => set({ liveEvents: [] }),
}))
