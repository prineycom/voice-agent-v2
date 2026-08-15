import { describe, expect, it } from 'vitest'
import {
  ACTIVE_LLM_MODEL_IDENTITY,
  CONTROL_VERSION,
  RealtimeControlGate,
  initialVoiceState,
  parseCapability,
  parseControlEvent,
  parseHealthReadinessReport,
  voiceReducer,
  type ComponentHealthObservation,
  type ControlEvent,
  type HealthReadinessReport,
  type SessionCapability,
} from './state'

const readyComponents: ComponentHealthObservation[] = [
  ['livekit', 'livekit-server-1.13.5', CONTROL_VERSION],
  ['controller', 'voice-agent-v2-controller', CONTROL_VERSION],
  ['stt', 'whisper-large-v3-turbo', 'voice-agent.stt.v1'],
  ['selected_llm', ACTIVE_LLM_MODEL_IDENTITY, 'voice-agent.llm-provider.v1'],
  ['tts', 'silero-kseniya', 'voice-agent.tts.v2'],
].map(([component, identity, contract_version]) => ({
  component: component as ComponentHealthObservation['component'],
  liveness: 'alive', readiness: 'ready', compatible: true,
  identity, contract_version, reason_code: null, retry_count: 0, retry_limit: 0,
}))

const readyHealth: HealthReadinessReport = {
  schema_version: 'voice-agent.health-readiness.v1',
  overall_readiness: 'ready',
  components: readyComponents,
  provider_mode: 'local', external_transfer: false, automatic_fallback: false,
  stt_location: 'local', tts_location: 'local', auth_boundary: 'tailnet',
  wake_enabled: false, selected_avatar_module: 'mvp-eye-svg-v1',
}

const capability: SessionCapability = {
  session_id: 'session-test-0001',
  stream_epoch: 1,
  livekit_url: 'wss://voice.test.ts.net:7443',
  token: 'x'.repeat(32),
  expires_in_seconds: 300,
  admission_timeout_ms: 30_000,
  control_version: CONTROL_VERSION,
  llm_profile: {
    provider_mode: 'local', model_identity: ACTIVE_LLM_MODEL_IDENTITY,
  },
  tts_profile: {
    profile: 'silero-kseniya', backend: 'silero', speaker: 'kseniya',
    output_sample_rate_hz: 48_000, native_sample_rate_hz: 48_000,
    license: 'CC-BY-NC-SA-4.0', private_noncommercial_only: true,
  },
}

function event(
  sequence: number,
  type: ControlEvent['type'],
  turnId = 'turn-00000001',
  payload: Record<string, unknown> = {},
  streamEpoch = 1,
): ControlEvent {
  const sessionEvent = type.startsWith('session.')
  const turnGeneration = sessionEvent ? 0 : Number(turnId.match(/(\d+)$/)?.[1] ?? 1)
  const effectivePayload = type === 'session.ready'
    ? { state: 'ready', user_state: 'available', health: readyHealth, ...payload }
    : payload
  return {
    schema_version: CONTROL_VERSION,
    session_id: capability.session_id,
    turn_id: sessionEvent ? 'session' : turnId,
    stream_epoch: streamEpoch,
    turn_generation: turnGeneration,
    request_id: sessionEvent ? 'session' : `request-${turnGeneration.toString().padStart(8, '0')}`,
    media_generation: turnGeneration,
    sequence,
    type,
    terminal: ['turn.completed', 'turn.interrupted', 'turn.failed'].includes(type),
    payload: effectivePayload,
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
    event(startSequence + 1, 'turn.media-ready', turnId, {
      server_media_publication_id: 'publication-test',
    }),
    event(startSequence + 2, 'stt.final', turnId, { transcript: user }),
    event(startSequence + 3, 'turn.thinking', turnId),
    event(startSequence + 4, 'llm.visible', turnId, {
      response: assistant,
      endpoint_to_first_visible_ms: 37,
    }),
    event(startSequence + 5, 'turn.speaking', turnId, {
      server_streamed_output: true,
      endpoint_to_first_accepted_pcm_ms: 64,
    }),
    event(startSequence + 6, 'turn.completed', turnId, {
      outcome: 'completed',
      endpoint_to_first_visible_ms: 37,
      endpoint_to_first_accepted_pcm_ms: 64,
    }),
  ]
}

describe('checkpoint A browser state', () => {
  it('accepts only the fixed validated LLM and Silero capability', () => {
    expect(parseCapability(capability)).toMatchObject({
      llm_profile: {
        provider_mode: 'local',
        model_identity: ACTIVE_LLM_MODEL_IDENTITY,
      },
      tts_profile: { profile: 'silero-kseniya' },
    })
    expect(parseCapability({
      ...capability,
      llm_profile: { ...capability.llm_profile, model_identity: 'unverified-model' },
    })).toBeNull()
    expect(parseCapability({
      ...capability,
      llm_profile: { ...capability.llm_profile, provider_mode: 'cloud' },
    })).toBeNull()
    expect(parseCapability({
      ...capability,
      tts_profile: { ...capability.tts_profile, profile: 'qwen-ryan' },
    })).toBeNull()
    expect(parseCapability({
      ...capability,
      tts_profile: { ...capability.tts_profile, output_sample_rate_hz: 16_000 },
    })).toBeNull()
  })

  it('separates liveness from compatible readiness and rejects false ready reports', () => {
    expect(parseHealthReadinessReport(readyHealth)?.overall_readiness).toBe('ready')
    const incompatible = {
      ...readyHealth,
      overall_readiness: 'unready',
      components: readyComponents.map((component) => component.component === 'selected_llm'
        ? { ...component, readiness: 'unready', compatible: false, reason_code: 'model_contract_incompatible' }
        : component),
    }
    expect(parseHealthReadinessReport(incompatible)).toMatchObject({
      overall_readiness: 'unready',
      components: expect.arrayContaining([
        expect.objectContaining({
          component: 'selected_llm', liveness: 'alive', readiness: 'unready', compatible: false,
        }),
      ]),
    })
    expect(parseHealthReadinessReport({ ...incompatible, overall_readiness: 'ready' })).toBeNull()
  })

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
      ...completedTurn(9, 'turn-00000002', 'Второй вопрос.', 'Второй ответ.'),
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
      event(3, 'turn.media-ready'),
      event(4, 'stt.final', 'turn-00000001', { transcript: 'Вопрос.' }),
      event(5, 'turn.thinking'),
      event(6, 'llm.visible', 'turn-00000001', { response: 'Видимый ответ.' }),
      event(7, 'turn.failed', 'turn-00000001', {
        outcome: 'failed', stage: 'tts', code: 'selected_tts_unavailable',
        user_state: 'degraded', retry_count: 0, retry_limit: 0,
      }),
    ])

    expect(state.connection).toBe('ready')
    expect(state.phase).toBe('idle')
    expect(state.availability).toBe('degraded')
    expect(state.history[0]).toMatchObject({
      assistant: 'Видимый ответ.',
      outcome: 'failed',
      audioUnavailable: true,
      userState: 'degraded',
    })
  })

  it('keeps speaking while later visible text arrives', () => {
    const state = apply([
      event(1, 'session.ready'),
      event(2, 'turn.listening'),
      event(3, 'turn.media-ready'),
      event(4, 'stt.final', 'turn-00000001', { transcript: 'Вопрос.' }),
      event(5, 'turn.thinking'),
      event(6, 'llm.visible', 'turn-00000001', { response: 'Первый ответ.' }),
      event(7, 'turn.speaking'),
      event(8, 'llm.visible', 'turn-00000001', {
        response: 'Первый ответ. Продолжение.',
      }),
    ])

    expect(state.phase).toBe('speaking')
    expect(state.response).toBe('Первый ответ. Продолжение.')
    expect(state.history[0].assistant).toBe('Первый ответ. Продолжение.')
  })

  it('records STT failure without inventing a transcript or degrading the session', () => {
    const state = apply([
      event(1, 'session.ready'),
      event(2, 'turn.listening'),
      event(3, 'turn.media-ready'),
      event(4, 'turn.failed', 'turn-00000001', {
        outcome: 'failed', stage: 'stt', code: 'selected_stt_unavailable',
        user_state: 'unavailable', retry_count: 0, retry_limit: 0,
      }),
    ])

    expect(state.connection).toBe('ready')
    expect(state.availability).toBe('unavailable')
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
    expect(gate.accept(event(3, 'turn.media-ready'))).toBe(true)
    expect(gate.accept(event(4, 'stt.final', 'turn-other'))).toBe(false)
    expect(gate.accept(event(4, 'stt.final'))).toBe(true)
    expect(gate.accept(event(4, 'turn.thinking'))).toBe(false)
  })

  it('keeps effective microphone truth and bounded transition errors in UI state', () => {
    let state = configured()
    state = voiceReducer(state, { type: 'microphone-lifecycle', status: 'live' })
    state = voiceReducer(state, {
      type: 'microphone', enabled: true, transitioning: false,
    })
    state = voiceReducer(state, { type: 'microphone-lifecycle', status: 'error' })
    state = voiceReducer(state, {
      type: 'microphone',
      enabled: true,
      transitioning: false,
      error: 'Не удалось выключить микрофон. Повторите попытку.',
    })

    expect(state.microphoneStatus).toBe('error')
    expect(state.microphoneAvailable).toBe(true)
    expect(state.microphoneEnabled).toBe(true)
    expect(state.microphoneTransitioning).toBe(false)
    expect(state.microphoneError).toBe('Не удалось выключить микрофон. Повторите попытку.')
  })

  it('preserves history while a reconnect interrupts the active item', () => {
    let state = apply([
      event(1, 'session.ready'),
      ...completedTurn(2, 'turn-00000001', 'Старый вопрос.', 'Старый ответ.'),
      event(9, 'turn.listening', 'turn-00000002'),
      event(10, 'turn.media-ready', 'turn-00000002'),
      event(11, 'stt.final', 'turn-00000002', { transcript: 'Новый вопрос.' }),
      event(12, 'turn.thinking', 'turn-00000002'),
    ])
    state = voiceReducer(state, {
      type: 'microphone', enabled: false, transitioning: false,
    })
    state = voiceReducer(state, { type: 'connection', connection: 'reconnecting' })
    state = voiceReducer(state, {
      type: 'control',
      event: event(13, 'session.reconnected', 'session', { state: 'ready' }, 2),
    })
    state = voiceReducer(state, {
      type: 'control',
      event: event(14, 'session.ready', 'session', { state: 'ready' }, 2),
    })

    expect(state.connection).toBe('ready')
    expect(state.history).toHaveLength(2)
    expect(state.history[0].outcome).toBe('completed')
    expect(state.history[1].outcome).toBe('interrupted')
    expect(state.microphoneEnabled).toBe(false)
  })
})
