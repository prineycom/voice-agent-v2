import { describe, expect, it } from 'vitest'
import {
  CONTROL_VERSION,
  RealtimeControlGate,
  initialVoiceState,
  parseCapability,
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
  turn_id = 'turn-00000001',
  payload: Record<string, unknown> = {},
  stream_epoch = 1,
): ControlEvent {
  return {
    schema_version: CONTROL_VERSION,
    session_id: capability.session_id,
    turn_id,
    stream_epoch,
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
  return events.reduce((state, item) => voiceReducer(state, { type: 'control', event: item }), configured())
}

describe('control event boundary', () => {
  it('parses only the closed bounded envelope', () => {
    const valid = JSON.stringify(event(1, 'turn.listening'))
    expect(parseControlEvent(valid)?.type).toBe('turn.listening')
    expect(parseControlEvent('{"schema_version":"wrong"}')).toBeNull()
    expect(parseControlEvent(new Uint8Array([0xff]))).toBeNull()
    const extra = { ...event(1, 'turn.listening'), endpoint: 'must-not-reach-browser' }
    expect(parseControlEvent(JSON.stringify(extra))).toBeNull()
    expect(parseControlEvent('x'.repeat(65_537))).toBeNull()
  })

  it('rejects malformed capability responses and accepts only WSS', () => {
    expect(parseCapability(capability)).toEqual(capability)
    expect(parseCapability({ ...capability, livekit_url: 'ws://127.0.0.1:7880' })).toBeNull()
    expect(parseCapability({ ...capability, provider_endpoint: 'http://private' })).toBeNull()
  })

  it('rejects out-of-order lifecycle events before they reach state or media actions', () => {
    const gate = new RealtimeControlGate(capability.session_id, 1)
    expect(gate.accept(event(1, 'session.ready', 'session'))).toBe(true)
    expect(gate.accept(event(2, 'turn.listening'))).toBe(true)
    expect(gate.accept(event(3, 'turn.speaking'))).toBe(false)
    expect(gate.accept(event(3, 'turn.transcribing'))).toBe(true)
    gate.beginReconnect()
    expect(gate.accept(event(4, 'stt.final', 'turn-00000001', {}, 1))).toBe(false)
    expect(gate.accept(event(5, 'session.reconnected', 'session', {}, 2))).toBe(true)
    expect(gate.accept(event(6, 'session.ready', 'session', {}, 1))).toBe(false)
  })

  it('shows correlated transcript and response and rejects duplicate/late/wrong-turn events', () => {
    let state = apply([
      event(1, 'session.ready', 'session'),
      event(2, 'turn.listening'),
      event(3, 'turn.transcribing'),
      event(4, 'stt.final', 'turn-00000001', { transcript: 'Привет.' }),
      event(5, 'turn.thinking'),
      event(6, 'llm.final', 'turn-00000001', { response: 'Здравствуйте.' }),
      event(7, 'turn.speaking'),
    ])
    expect(state.transcript).toBe('Привет.')
    expect(state.response).toBe('Здравствуйте.')
    expect(state.phase).toBe('speaking')

    state = voiceReducer(state, { type: 'control', event: event(7, 'turn.speaking') })
    state = voiceReducer(state, { type: 'control', event: event(6, 'llm.final') })
    state = voiceReducer(state, {
      type: 'control',
      event: event(8, 'turn.completed', 'turn-other'),
    })
    expect(state.droppedEvents).toBe(3)
    expect(state.phase).toBe('speaking')
  })

  it('terminates an interrupted turn before accepting a clean new turn', () => {
    const state = apply([
      event(1, 'session.ready', 'session'),
      event(2, 'turn.listening'),
      event(3, 'turn.transcribing'),
      event(4, 'stt.final'),
      event(5, 'turn.thinking'),
      event(6, 'llm.final'),
      event(7, 'turn.speaking'),
      event(8, 'turn.interrupted'),
      event(9, 'turn.listening', 'turn-00000002'),
      event(10, 'turn.transcribing', 'turn-00000002'),
      event(11, 'stt.final', 'turn-00000002', { transcript: 'Новая реплика.' }),
      event(12, 'turn.thinking', 'turn-00000002'),
      event(13, 'llm.final', 'turn-00000002', { response: 'Новый ответ.' }),
      event(14, 'turn.speaking', 'turn-00000002'),
      event(15, 'turn.playout-ready', 'turn-00000002'),
      event(16, 'turn.playout-retired', 'turn-00000002'),
      event(17, 'turn.completed', 'turn-00000002'),
    ])
    expect(state.currentTurnId).toBe('turn-00000002')
    expect(state.phase).toBe('completed')
    expect(state.transcript).toBe('Новая реплика.')
    expect(state.response).toBe('Новый ответ.')
    expect(state.droppedEvents).toBe(0)
  })

  it('clears stale content on reconnect and accepts only the next epoch', () => {
    let state = apply([
      event(1, 'session.ready', 'session'),
      event(2, 'turn.listening'),
      event(3, 'turn.transcribing'),
      event(4, 'stt.final', 'turn-00000001', { transcript: 'Старый текст.' }),
    ])
    state = voiceReducer(state, { type: 'connection', connection: 'reconnecting' })
    expect(state.transcript).toBe('')
    state = voiceReducer(state, {
      type: 'control',
      event: event(5, 'turn.listening', 'turn-old-replayed', {}, 1),
    })
    expect(state.phase).toBe('idle')
    expect(state.droppedEvents).toBe(1)
    state = voiceReducer(state, {
      type: 'control',
      event: event(6, 'session.reconnected', 'session', {
        state: 'awaiting_media',
        media_generation: 1,
        interrupted_turn_id: 'turn-00000001',
      }, 2),
    })
    expect(state.streamEpoch).toBe(2)
    expect(state.connection).toBe('reconnecting')
    const oldEpoch = event(7, 'session.ready', 'session', {}, 1)
    state = voiceReducer(state, { type: 'control', event: oldEpoch })
    expect(state.droppedEvents).toBe(2)
    state = voiceReducer(state, {
      type: 'control',
      event: event(8, 'session.ready', 'session', {
        state: 'ready', media_generation: 1,
      }, 2),
    })
    expect(state.connection).toBe('ready')
  })
})
