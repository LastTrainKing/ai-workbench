/**
 * Connection awareness: the UI must not die with the backend.
 *
 * A desktop application whose window turns into an error page because a service
 * restarted is a bad desktop application. The backend here is a real process that
 * can be stopped, restarted, or crash — and when it does, the window should say
 * so calmly and keep working with whatever it already has.
 *
 * Three behaviours, in order of importance:
 *
 * 1. **The window stays usable.** Nothing here unmounts a page or clears data.
 * 2. **The user is told, in one place.** A single banner with the time of the
 *    last successful contact, so "is it me or is it it" has an answer.
 * 3. **Recovery is automatic.** Polling backs off while down and resumes when the
 *    health check passes; the banner clears itself.
 *
 * The health probe is deliberately the cheapest endpoint available and does not
 * go through the normal request pipeline — a probe that fails for the same reason
 * the requests fail tells you nothing.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

const HEALTH_URL = '/api/v1/health'
/** Backoff schedule while unreachable, in milliseconds. */
const BACKOFF = [2000, 3000, 5000, 8000, 13000, 21000]
/** Interval once healthy. */
const HEALTHY_INTERVAL = 15000

export interface ConnectionState {
  online: boolean
  /** Wall-clock time of the last successful contact, or null if never. */
  lastSeen: Date | null
  /** How many consecutive failures so far. */
  failures: number
  /** Seconds until the next attempt, for the banner to count down. */
  retryIn: number
  checkNow: () => void
}

export function useConnection(): ConnectionState {
  const [online, setOnline] = useState(true)
  const [lastSeen, setLastSeen] = useState<Date | null>(null)
  const [failures, setFailures] = useState(0)
  const [retryIn, setRetryIn] = useState(0)

  const timer = useRef<number | null>(null)
  const ticker = useRef<number | null>(null)
  const alive = useRef(true)
  const failuresRef = useRef(0)

  const schedule = useCallback((delay: number) => {
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => void probe(), delay)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const probe = useCallback(async () => {
    if (!alive.current) return
    try {
      const response = await fetch(HEALTH_URL, { method: 'GET', cache: 'no-store' })
      if (!alive.current) return
      if (response.ok) {
        failuresRef.current = 0
        setFailures(0)
        setOnline(true)
        setLastSeen(new Date())
        schedule(HEALTHY_INTERVAL)
        return
      }
      throw new Error(`health ${response.status}`)
    } catch {
      if (!alive.current) return
      failuresRef.current += 1
      setFailures(failuresRef.current)
      setOnline(false)
      const delay = BACKOFF[Math.min(failuresRef.current - 1, BACKOFF.length - 1)]
      schedule(delay)
    }
  }, [schedule])

  useEffect(() => {
    alive.current = true
    void probe()

    // A one-second ticker so the banner can show "3s 后重试" without re-rendering
    // on every frame.
    ticker.current = window.setInterval(() => {
      setRetryIn((value) => (value > 0 ? value - 1 : value))
    }, 1000)

    return () => {
      alive.current = false
      if (timer.current !== null) window.clearTimeout(timer.current)
      if (ticker.current !== null) window.clearInterval(ticker.current)
    }
  }, [probe])

  // Re-probe immediately when the window regains focus: the user coming back to
  // the window is a strong signal that they want current data.
  useEffect(() => {
    const onFocus = () => void probe()
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [probe])

  const checkNow = useCallback(() => {
    failuresRef.current = 0
    setFailures(0)
    void probe()
  }, [probe])

  return { online, lastSeen, failures, retryIn, checkNow }
}

/** `14:32:05`, or a plain "还没有成功连接过" when there has never been one. */
export function formatLastSeen(when: Date | null): string {
  if (!when) return '尚未成功连接'
  return when.toLocaleTimeString('zh-CN', { hour12: false })
}
