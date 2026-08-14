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
      phase: 'listening',
      microphoneAvailable: true,
      microphoneEnabled: true,
      response: 'Ответ.',
      currentTurnTerminal: false,
    }, avatarReady)

    expect(model).toMatchObject({
      connectionLabel: 'READY',
      avatarLifecycle: 'listening',
      microphoneVisual: 'listening',
      response: 'Ответ.',
    })
    expect(model.components.every((component) => component.health === 'READY')).toBe(true)
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
      microphoneAvailable: true,
      microphoneEnabled: true,
      microphoneError: 'transition failed',
    }, avatarReady).microphoneVisual).toBe('error')
  })

  it('reports fallback health without changing the voice path state', () => {
    const model = mapVoiceStateToUi({
      ...initialVoiceState,
      connection: 'ready',
    }, { ...avatarReady, status: 'degraded', usingFallback: true })

    expect(model.connection).toBe('ready')
    expect(model.components.find((component) => component.id === 'avatar')?.health).toBe('DEGRADED')
  })
})
