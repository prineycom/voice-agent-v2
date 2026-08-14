import { useEffect, useState } from 'react'
import type { VoiceState } from '../state'

export const TURN_FAILURE_LIFECYCLE_MS = 2_000

export function useTurnFailureLifecycle(state: VoiceState): boolean {
  const failureKey = state.currentTurnTerminal && state.lastTurnEvent === 'turn.failed'
    ? `${state.sessionId ?? 'session'}:${state.streamEpoch}:${state.lastSequence}`
    : null
  const [expiredFailureKey, setExpiredFailureKey] = useState<string | null>(null)

  useEffect(() => {
    if (failureKey === null) return
    const timer = window.setTimeout(
      () => setExpiredFailureKey(failureKey),
      TURN_FAILURE_LIFECYCLE_MS,
    )
    return () => window.clearTimeout(timer)
  }, [failureKey])

  return failureKey !== null && failureKey !== expiredFailureKey
}
