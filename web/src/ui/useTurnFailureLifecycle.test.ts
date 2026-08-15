import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { initialVoiceState, type VoiceState } from '../state'
import { TURN_FAILURE_LIFECYCLE_MS, useTurnFailureLifecycle } from './useTurnFailureLifecycle'

function failedTurn(lastSequence: number): VoiceState {
  return {
    ...initialVoiceState,
    connection: 'ready',
    sessionId: 'session-test',
    streamEpoch: 1,
    lastSequence,
    currentTurnTerminal: true,
    lastTurnEvent: 'turn.failed',
  }
}

afterEach(() => vi.useRealTimers())

describe('turn failure avatar lifecycle', () => {
  it('shows each failure immediately and returns to neutral after two seconds', () => {
    vi.useFakeTimers()
    const { result, rerender } = renderHook(
      ({ state }: { state: VoiceState }) => useTurnFailureLifecycle(state),
      { initialProps: { state: failedTurn(4) } },
    )

    expect(result.current).toBe(true)
    act(() => vi.advanceTimersByTime(TURN_FAILURE_LIFECYCLE_MS - 1))
    expect(result.current).toBe(true)
    act(() => vi.advanceTimersByTime(1))
    expect(result.current).toBe(false)

    rerender({ state: failedTurn(5) })
    expect(result.current).toBe(true)
  })
})
