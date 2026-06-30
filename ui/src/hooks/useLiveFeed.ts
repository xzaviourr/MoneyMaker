import { useEffect, useState } from 'react'
import { useStore } from './useStore'

export interface LiveEvent {
  type:    string
  source:  string
  payload: unknown
  ts:      string
}

export function useLiveFeed() {
  const [connected, setConnected] = useState(false)
  const addEvent = useStore(s => s.addLiveEvent)

  useEffect(() => {
    let ws: WebSocket | null = null
    let timer: ReturnType<typeof setTimeout> | null = null

    function connect() {
      try {
        ws = new WebSocket('ws://localhost:8000/ws/live')

        ws.onopen = () => setConnected(true)
        ws.onclose = () => {
          setConnected(false)
          timer = setTimeout(connect, 3000)
        }
        ws.onerror = () => ws?.close()
        ws.onmessage = (e) => {
          try {
            const evt: LiveEvent = JSON.parse(e.data)
            if (evt.type !== 'pong') addEvent(evt)
          } catch { }
        }
      } catch {
        timer = setTimeout(connect, 3000)
      }
    }

    connect()
    return () => {
      ws?.close()
      if (timer) clearTimeout(timer)
    }
  }, [addEvent])

  return { connected }
}
