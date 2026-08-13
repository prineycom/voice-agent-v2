import { describe, expect, it } from 'vitest'
import {
  CONTROL_VERSION,
  RealtimeControlGate,
  initialVoiceState,
  parseControlEvent,
  voiceReducer,
  type ControlEvent,
  type SessionCapability,
} from './state'

const capability: SessionCapability = {
  session_id: 'session-test-0001',
  stream_epoch: 1,
  livekit_url: 'wss://voice.test.ts.net:7443',
  token: 'x'.repeat(32),
  expires_in_seconds: 300,
  admission_timeout_ms: 30_000,
  control_version: CONTROL_VERSION,
}

function event(
  sequence: number,
  type: ControlEvent['type'],
  turnId = 'turn-00000001',
  payload: Record<string, unknown> = {},
  streamEpoch = 1,
): ControlEvent {
  return {
    schema_version: CONTROL_VERSION,
    session_id: capability.session_id,
    turn_id: type.startsWith('session.') ? 'session' : turnId,
    stream_epoch: streamEpoch,
    sequence,
    type,
    terminal: ['turn.completed', 'turn.interrupted', 'turn.failed'].includes(type),
    payload,
  }
}

function configured() {
  return voiceReducer(initialVoiceState, { type: 'session-created', capability })
}

function apply(events: ControlEvent[]) {
  return events.reduce(
    (state, item) => voiceReducer(state, { type: 'control', event: item }),
    configured(),
  )
}

function completedTurn(startSequence: number, turnId: string, user: string, assistant: string) {
  return [
    event(startSequence, 'turn.listening', turnId),
    event(startSequence + 1, 'stt.final', turnId, { transcript: user }),
    event(startSequence + 2, 'turn.thinking', turnId),
    event(startSequence + 3, 'llm.visible', turnId, {
      response: assistant,
      endpoint_to_first_visible_ms: 37,
    }),
    event(startSequence + 4, 'turn.speaking', turnId, {
      server_streamed_output: true,
      endpoint_to_first_accepted_pcm_ms: 64,
    }),
    event(startSequence + 5, 'turn.completed', turnId, {
      outcome: 'completed',
      endpoint_to_first_visible_ms: 37,
      endpoint_to_first_accepted_pcm_ms: 64,
    }),
  ]
}

describe('checkpoint A browser state', () => {
  it('rejects removed playout events at the serialized protocol boundary', () => {
    expect(parseControlEvent(JSON.stringify(event(1, 'turn.listening')))?.type).toBe('turn.listening')
    const removed = {
      ...event(2, 'turn.completed'),
      type: 'turn.playout-ready',
      terminal: false,
    }
    expect(parseControlEvent(JSON.stringify(removed))).toBeNull()
  })

  it('keeps chronological in-memory history with outcomes and server metrics', () => {
    const state = apply([
      event(1, 'session.ready'),
      ...completedTurn(2, 'turn-00000001', 'Первый вопрос.', 'Первый ответ.'),
      ...completedTurn(8, 'turn-00000002', 'Второй вопрос.', 'Второй ответ.'),
    ])

    expect(state.phase).toBe('idle')
    expect(state.history).toEqual([
      expect.objectContaining({
        turnId: 'turn-00000001',
        user: 'Первый вопрос.',
        assistant: 'Первый ответ.',
        outcome: 'completed',
        endpointToFirstVisibleMs: 37,
        endpointToFirstAcceptedPcmMs: 64,
      }),
      expect.objectContaining({
        turnId: 'turn-00000002',
        user: 'Второй вопрос.',
        assistant: 'Второй ответ.',
        outcome: 'completed',
      }),
    ])
  })

  it('retains visible text and marks only the item when TTS fails', () => {
    const state = apply([
      event(1, 'session.ready'),
      event(2, 'turn.listening'),
      event(3, 'stt.final', 'turn-00000001', { transcript: 'Вопрос.' }),
      event(4, 'turn.thinking'),
      event(5, 'llm.visible', 'turn-00000001', { response: 'Видимый ответ.' }),
      event(6, 'turn.failed', 'turn-00000001', {
        outcome: 'failed', stage: 'tts', code: 'selected_tts_unavailable',
      }),
    ])

    expect(state.connection).toBe('ready')
    expect(state.phase).toBe('idle')
    expect(state.history[0]).toMatchObject({
      assistant: 'Видимый ответ.',
      outcome: 'failed',
      audioUnavailable: true,
    })
  })

  it('records STT failure without inventing a transcript or degrading the session', () => {
    const state = apply([
      event(1, 'session.ready'),
      event(2, 'turn.listening'),
      event(3, 'turn.failed', 'turn-00000001', {
        outcome: 'failed', stage: 'stt', code: 'selected_stt_unavailable',
      }),
    ])

    expect(state.connection).toBe('ready')
    expect(state.phase).toBe('idle')
    expect(state.currentTurnTerminal).toBe(true)
    expect(state.history).toEqual([
      expect.objectContaining({
        turnId: 'turn-00000001',
        user: '',
        assistant: '',
        outcome: 'failed',
        audioUnavailable: false,
      }),
    ])
  })

  it('drops duplicate, wrong-turn, and stale-sequence events', () => {
    const gate = new RealtimeControlGate(capability.session_id, 1)
    expect(gate.accept(event(1, 'session.ready'))).toBe(true)
    expect(gate.accept(event(2, 'turn.listening'))).toBe(true)
    expect(gate.accept(event(3, 'stt.final', 'turn-other'))).toBe(false)
    expect(gate.accept(event(3, 'stt.final'))).toBe(true)
    expect(gate.accept(event(3, 'turn.thinking'))).toBe(false)
  })

  it('keeps effective microphone truth and bounded transition errors in UI state', () => {
    let state = configured()
    state = voiceReducer(state, {
      type: 'microphone', enabled: true, transitioning: false,
    })
    state = voiceReducer(state, {
      type: 'microphone',
      enabled: true,
      transitioning: false,
      error: 'Не удалось выключить микрофон. Повторите попытку.',
    })

    expect(state.microphoneAvailable).toBe(true)
    expect(state.microphoneEnabled).toBe(true)
    expect(state.microphoneTransitioning).toBe(false)
    expect(state.microphoneError).toBe('Не удалось выключить микрофон. Повторите попытку.')
  })

  it('preserves history while a reconnect interrupts the active item', () => {
    let state = apply([
      event(1, 'session.ready'),
      ...completedTurn(2, 'turn-00000001', 'Старый вопрос.', 'Старый ответ.'),
      event(8, 'turn.listening', 'turn-00000002'),
      event(9, 'stt.final', 'turn-00000002', { transcript: 'Новый вопрос.' }),
      event(10, 'turn.thinking', 'turn-00000002'),
    ])
    state = voiceReducer(state, {
      type: 'microphone', enabled: false, transitioning: false,
    })
    state = voiceReducer(state, { type: 'connection', connection: 'reconnecting' })
    state = voiceReducer(state, {
      type: 'control',
      event: event(11, 'session.reconnected', 'session', { state: 'ready' }, 2),
    })
    state = voiceReducer(state, {
      type: 'control',
      event: event(12, 'session.ready', 'session', { state: 'ready' }, 2),
    })

    expect(state.connection).toBe('ready')
    expect(state.history).toHaveLength(2)
    expect(state.history[0].outcome).toBe('completed')
    expect(state.history[1].outcome).toBe('interrupted')
    expect(state.microphoneEnabled).toBe(false)
  })
})
