/**
 * Live state for one autonomous run.
 *
 * The backend streams events with monotonically increasing sequence numbers, so
 * this hook keeps only the last one it has seen and asks for everything after it.
 * That keeps a run emitting hundreds of events from re-rendering its whole
 * history on every poll.
 *
 * Polling stops as soon as the run reaches a terminal state, and on unmount — a
 * run left polling in a background tab would keep a request every second forever.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { agentApi, isTerminal } from '@/lib/agentApi'
import type { Confirmation, DiffResult, RunEvent, RunState, RunStatus } from '@/lib/agentApi'
import { toApiError } from '@/lib/api'

const POLL_MS = 1200
/** How many events to keep in memory. The backend caps at 2000; this is tighter. */
const MAX_EVENTS = 400

export interface AgentRunController {
  runId: string | null
  status: RunStatus | null
  events: RunEvent[]
  diff: DiffResult | null
  confirmations: Confirmation[]
  starting: boolean
  error: string | null

  start: (goal: string) => Promise<void>
  pause: () => Promise<void>
  resume: () => Promise<void>
  cancel: () => Promise<void>
  decide: (id: string, approved: boolean) => Promise<void>
  refreshDiff: () => Promise<void>
  reset: () => void
}

export function useAgentRun(): AgentRunController {
  const [runId, setRunId] = useState<string | null>(null)
  const [status, setStatus] = useState<RunStatus | null>(null)
  const [events, setEvents] = useState<RunEvent[]>([])
  const [diff, setDiff] = useState<DiffResult | null>(null)
  const [confirmations, setConfirmations] = useState<Confirmation[]>([])
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Refs rather than state: the polling loop reads these and must not be
  // restarted (or capture a stale value) when they change.
  const lastSeq = useRef(0)
  const finished = useRef(false)
  const timer = useRef<number | null>(null)
  const alive = useRef(true)

  const stopPolling = useCallback(() => {
    if (timer.current !== null) {
      window.clearInterval(timer.current)
      timer.current = null
    }
  }, [])

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      stopPolling()
    }
  }, [stopPolling])

  const poll = useCallback(async () => {
    if (!runId) return
    try {
      const [state, batch, pending] = await Promise.all([
        agentApi.status(runId),
        agentApi.events(runId, lastSeq.current),
        agentApi.confirmations(runId).catch(() => ({ confirmations: [], count: 0 })),
      ])
      if (!alive.current) return

      setStatus(state)
      if (batch.events.length) {
        lastSeq.current = batch.last_seq
        setEvents((prev) => [...prev, ...batch.events].slice(-MAX_EVENTS))
      }
      setConfirmations(pending.confirmations)

      if (batch.finished || isTerminal(state.state)) {
        finished.current = true
        stopPolling()
        // The controller reaches a terminal state *before* the backend has
        // finished persisting and emitting the final `result` event, so a poll
        // that observes "done" can arrive in the gap between the two. A single
        // delayed fetch is not enough either — persistence takes as long as it
        // takes — so this retries briefly until the event lands. Without it the
        // result panel renders empty for every run, which is exactly what the
        // real browser test caught and typechecking cannot.
        void collectFinalEvents()
        // The diff is only meaningful once the workspace has stopped changing.
        agentApi.diff(runId).then(setDiff).catch(() => undefined)
      }
    } catch (exc) {
      // A run that ends while the backend restarts is not an error the user needs
      // to see repeatedly; the next poll either succeeds or the run is gone.
      const apiError = toApiError(exc)
      if (!alive.current) return
      if (!finished.current && apiError.status !== 404) {
        setError(apiError.message)
      }
      if (apiError.status === 404) {
        finished.current = true
        stopPolling()
      }
    }
  }, [runId, stopPolling])

  /**
   * After the run reports a terminal state, keep asking for events for a short
   * while until the backend's final `result` event arrives.
   */
  const collectFinalEvents = useCallback(async () => {
    if (!runId) return
    for (let attempt = 0; attempt < 10; attempt++) {
      await new Promise((resolve) => window.setTimeout(resolve, 700))
      if (!alive.current) return
      try {
        const batch = await agentApi.events(runId, lastSeq.current)
        if (batch.events.length) {
          lastSeq.current = batch.last_seq
          setEvents((prev) => [...prev, ...batch.events].slice(-MAX_EVENTS))
          if (batch.events.some((e) => e.kind === 'result')) return
        }
      } catch {
        /* the run is over; a failed final fetch must not surface as an error */
      }
    }
  }, [runId])

  useEffect(() => {
    if (!runId || finished.current) return
    void poll()
    timer.current = window.setInterval(() => void poll(), POLL_MS)
    return stopPolling
  }, [runId, poll, stopPolling])

  const start = useCallback(async (goal: string) => {
    const trimmed = goal.trim()
    if (!trimmed) return
    setStarting(true)
    setError(null)
    setEvents([])
    setDiff(null)
    setConfirmations([])
    lastSeq.current = 0
    finished.current = false
    try {
      // `requestResult` resolves with `{ data }` and throws on failure, so the
      // success path needs no branch here — the catch below handles the rest.
      const result = await agentApi.start(trimmed)
      setRunId(result.data.run_id)
    } catch (exc) {
      setError(toApiError(exc).message)
    } finally {
      setStarting(false)
    }
  }, [])

  const control = useCallback(
    async (action: 'pause' | 'resume' | 'cancel') => {
      if (!runId) return
      try {
        await agentApi[action](runId)
        // Pausing should feel immediate, so reflect it without waiting for a poll.
        setStatus((prev) =>
          prev
            ? {
                ...prev,
                paused: action === 'pause',
                state: (action === 'pause'
                  ? 'PAUSED'
                  : action === 'cancel'
                    ? 'CANCELLED'
                    : 'EXECUTING') as RunState,
              }
            : prev,
        )
        if (action === 'resume') void poll()
      } catch (exc) {
        setError(toApiError(exc).message)
      }
    },
    [runId, poll],
  )

  const decide = useCallback(async (id: string, approved: boolean) => {
    try {
      await agentApi.decide(id, approved)
      setConfirmations((prev) => prev.filter((c) => c.id !== id))
    } catch (exc) {
      setError(toApiError(exc).message)
    }
  }, [])

  const refreshDiff = useCallback(async () => {
    if (!runId) return
    try {
      setDiff(await agentApi.diff(runId))
    } catch {
      /* the diff is supplementary; failing to load it must not break the page */
    }
  }, [runId])

  const reset = useCallback(() => {
    stopPolling()
    finished.current = false
    lastSeq.current = 0
    setRunId(null)
    setStatus(null)
    setEvents([])
    setDiff(null)
    setConfirmations([])
    setError(null)
  }, [stopPolling])

  return {
    runId,
    status,
    events,
    diff,
    confirmations,
    starting,
    error,
    start,
    pause: () => control('pause'),
    resume: () => control('resume'),
    cancel: () => control('cancel'),
    decide,
    refreshDiff,
    reset,
  }
}
