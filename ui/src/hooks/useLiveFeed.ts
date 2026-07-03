import { useEffect, useRef, useState } from 'react'
import { useStore } from './useStore'

export interface LiveEvent {
  type:    string
  source:  string
  payload: unknown
  ts:      string
}

function getWsUrl(): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host  = window.location.hostname
  const port  = window.location.port || (proto === 'wss:' ? '443' : '80')
  // In dev (Vite), the UI is on :3000 but the backend is on :8000
  const backendPort = (host === 'localhost' || host === '127.0.0.1') ? '8000' : port
  return `${proto}//${host}:${backendPort}/ws/live`
}

const _MIN_DELAY = 1_000
const _MAX_DELAY = 30_000

export function useLiveFeed() {
  const [connected, setConnected] = useState(false)
  const addEvent = useStore(s => s.addLiveEvent)
  const delayRef = useRef(_MIN_DELAY)

  useEffect(() => {
    let ws: WebSocket | null = null
    let timer: ReturnType<typeof setTimeout> | null = null
    let stopped = false

    function connect() {
      if (stopped) return
      try {
        ws = new WebSocket(getWsUrl())

        ws.onopen = () => {
          setConnected(true)
          delayRef.current = _MIN_DELAY
        }
        ws.onclose = () => {
          setConnected(false)
          if (!stopped) {
            timer = setTimeout(() => {
              delayRef.current = Math.min(delayRef.current * 2, _MAX_DELAY)
              connect()
            }, delayRef.current)
          }
        }
        ws.onerror = () => ws?.close()
        ws.onmessage = (e) => {
          try {
            const evt: LiveEvent = JSON.parse(e.data)
            if (evt.type !== 'pong') addEvent(evt)
          } catch { }
        }
      } catch {
        if (!stopped) {
          timer = setTimeout(() => {
            delayRef.current = Math.min(delayRef.current * 2, _MAX_DELAY)
            connect()
          }, delayRef.current)
        }
      }
    }

    connect()
    return () => {
      stopped = true
      ws?.close()
      if (timer) clearTimeout(timer)
    }
  }, [addEvent])

  return { connected }
}
