import { describe, expect, it } from 'vitest'
import type { AvatarHealthV1 } from '../avatar/contract'
import { initialVoiceState } from '../state'
import { mapVoiceStateToUi } from './stateMapping'

const avatarReady: AvatarHealthV1 = {
  status: 'ready',
  activeModuleId: 'mvp-eye-svg-v1',
  usingFallback: false,
  rejectedInputs: 0,
  renderFailures: 0,
}

describe('voice reducer to UI domain mapping', () => {
  it('maps listening, microphone, speech, history and component readiness explicitly', () => {
    const model = mapVoiceStateToUi({
      ...initialVoiceState,
      connection: 'ready',
      availability: 'available',
      phase: 'listening',
      microphoneStatus: 'live',
      microphoneAvailable: true,
      microphoneEnabled: true,
      response: 'Ответ.',
      currentTurnTerminal: false,
    }, avatarReady)

    expect(model).toMatchObject({
      connectionLabel: 'READY',
      avatarLifecycle: 'listening',
      microphoneVisual: 'listening',
      microphoneStatusLabel: 'MIC LISTENING',
      response: 'Ответ.',
      llmProviderSummary: 'UNAVAILABLE',
      llmModelSummary: 'UNAVAILABLE',
    })
    expect(model.components.every((component) => component.health === 'READY')).toBe(true)
    expect(model.components.map((component) => component.id)).toEqual([
      'livekit', 'controller', 'stt', 'selected_llm', 'tts',
      'avatar_host', 'active_module',
    ])
  })

  it('gives reconnect, interruption and microphone failure their safe visual states', () => {
    expect(mapVoiceStateToUi({
      ...initialVoiceState,
      connection: 'reconnecting',
    }, avatarReady).avatarLifecycle).toBe('reconnecting')

    expect(mapVoiceStateToUi({
      ...initialVoiceState,
      connection: 'ready',
      currentTurnTerminal: true,
      lastTurnEvent: 'turn.interrupted',
    }, avatarReady).avatarLifecycle).toBe('interrupted')

    const failedTurn = {
      ...initialVoiceState,
      connection: 'ready' as const,
      currentTurnTerminal: true,
      lastTurnEvent: 'turn.failed' as const,
    }
    expect(mapVoiceStateToUi(failedTurn, avatarReady, true).avatarLifecycle).toBe('error')
    expect(mapVoiceStateToUi(failedTurn, avatarReady, false).avatarLifecycle).toBe('idle')

    expect(mapVoiceStateToUi({
      ...initialVoiceState,
      connection: 'ready',
      microphoneStatus: 'error',
      microphoneAvailable: true,
      microphoneEnabled: true,
      microphoneError: 'transition failed',
    }, avatarReady).microphoneVisual).toBe('error')
  })

  it('exposes permission, publication, live, muted, and error microphone states without ambiguity', () => {
    const label = (microphoneStatus: typeof initialVoiceState.microphoneStatus) => mapVoiceStateToUi({
      ...initialVoiceState,
      connection: 'connecting',
      microphoneStatus,
      microphoneEnabled: microphoneStatus === 'live',
    }, avatarReady).microphoneStatusLabel

    expect(label('disconnected')).toBe('MIC DISCONNECTED')
    expect(label('requesting-permission')).toBe('MIC PERMISSION')
    expect(label('publishing')).toBe('MIC PUBLISHING')
    expect(label('live')).toBe('MIC LIVE')
    expect(label('muted')).toBe('MIC MUTED')
    expect(label('error')).toBe('MIC ERROR')
  })

  it('renders a late or duplicate control as a soft controller degradation', () => {
    const model = mapVoiceStateToUi({
      ...initialVoiceState,
      connection: 'ready',
      availability: 'degraded',
      failureStage: 'controller',
      failureCode: 'late_or_duplicate_event',
      droppedEvents: 1,
    }, avatarReady)

    expect(model.connectionLabel).toBe('DEGRADED')
    expect(model.components.find((component) => component.id === 'controller')).toMatchObject({
      health: 'DEGRADED', reason: 'late_or_duplicate_event',
    })
    expect(model.droppedEvents).toBe(1)
  })

  it('reports fallback health without changing the voice path state', () => {
    const model = mapVoiceStateToUi({
      ...initialVoiceState,
      connection: 'ready',
      availability: 'available',
    }, { ...avatarReady, status: 'degraded', usingFallback: true })

    expect(model.connection).toBe('ready')
    expect(model.connectionLabel).toBe('DEGRADED')
    expect(model.components.find((component) => component.id === 'active_module')?.health).toBe('DEGRADED')
  })

  it('reports unavailable speech-envelope analysis as avatar degradation', () => {
    const model = mapVoiceStateToUi({
      ...initialVoiceState,
      connection: 'ready',
      availability: 'available',
      speechEnvelopeStatus: 'unavailable',
    }, avatarReady)

    expect(model.audioBlocked).toBe(false)
    expect(model.speechEnvelopeStatus).toBe('unavailable')
    expect(model.components.find((component) => component.id === 'avatar_host')?.health).toBe('DEGRADED')
  })
})
