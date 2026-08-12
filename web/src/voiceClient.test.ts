import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const livekit = vi.hoisted(() => {
  const rooms: FakeRoom[] = []
  const createLocalAudioTrack = vi.fn()

  class FakeRoom {
    connect = vi.fn().mockResolvedValue(undefined)
    disconnect = vi.fn().mockResolvedValue(undefined)
    startAudio = vi.fn().mockResolvedValue(undefined)
    localParticipant = {
      publishTrack: vi.fn().mockResolvedValue(undefined),
      publishData: vi.fn().mockResolvedValue(undefined),
    }
    remoteParticipants = new Map([
      ['agent-session-test-0001', { identity: 'agent-session-test-0001' }],
    ])
    private handlers = new Map<string, Array<(...args: any[]) => void>>()

    constructor() {
      rooms.push(this)
    }

    on(event: string, callback: (...args: any[]) => void): this {
      const handlers = this.handlers.get(event) ?? []
      handlers.push(callback)
      this.handlers.set(event, handlers)
      return this
    }

    emit(event: string, ...args: any[]): void {
      for (const callback of this.handlers.get(event) ?? []) callback(...args)
    }
  }

  return { FakeRoom, createLocalAudioTrack, rooms }
})

vi.mock('livekit-client', () => ({
  Room: livekit.FakeRoom,
  RoomEvent: {
    TrackSubscribed: 'trackSubscribed',
    TrackUnsubscribed: 'trackUnsubscribed',
    ParticipantDisconnected: 'participantDisconnected',
    DataReceived: 'dataReceived',
    Reconnecting: 'reconnecting',
    Reconnected: 'reconnected',
    Disconnected: 'disconnected',
    MediaDevicesError: 'mediaDevicesError',
  },
  Track: {
    Kind: { Audio: 'audio' },
    Source: { Microphone: 'microphone' },
  },
  createLocalAudioTrack: livekit.createLocalAudioTrack,
}))

import { AudioPlaybackBoundary } from './playback'
import { VoiceClient, type VoiceClientCallbacks } from './voiceClient'

function capabilityResponse(): Response {
  return {
    ok: true,
    json: vi.fn().mockResolvedValue({
      session_id: 'session-test-0001',
      stream_epoch: 1,
      livekit_url: 'wss://voice.test.ts.net:7443',
      token: 'room-token-long-enough',
      expires_in_seconds: 30,
      admission_timeout_ms: 30_000,
      control_version: 'voice-agent.realtime-control.v1',
    }),
  } as unknown as Response
}

function callbacks(): VoiceClientCallbacks {
  return {
    onSession: vi.fn(),
    onConnection: vi.fn(),
    onControl: vi.fn(),
    onDrop: vi.fn(),
    onAudioBlocked: vi.fn(),
    onDiagnostic: vi.fn(),
  }
}

function emitControl(
  room: { emit(event: string, ...args: any[]): void },
  type: string,
  sequence: number,
  payload: Record<string, unknown> = {},
  terminal = false,
  streamEpoch = 1,
): void {
  room.emit(
    'dataReceived',
    new TextEncoder().encode(JSON.stringify({
      schema_version: 'voice-agent.realtime-control.v1',
      session_id: 'session-test-0001',
      turn_id: type.startsWith('session.') ? 'session' : 'turn-00000001',
      stream_epoch: streamEpoch,
      sequence,
      type,
      terminal,
      payload,
    })),
    { identity: 'agent-session-test-0001' },
    undefined,
    'voice-agent.control.v1',
  )
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(capabilityResponse()))
  livekit.createLocalAudioTrack.mockResolvedValue({ stop: vi.fn() })
  vi.spyOn(AudioPlaybackBoundary.prototype, 'setTrack').mockImplementation(() => undefined)
  vi.spyOn(AudioPlaybackBoundary.prototype, 'reset').mockImplementation(() => undefined)
  vi.spyOn(AudioPlaybackBoundary.prototype, 'suspend').mockImplementation(() => undefined)
  vi.spyOn(AudioPlaybackBoundary.prototype, 'dispose').mockResolvedValue(undefined)
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  livekit.rooms.length = 0
  livekit.createLocalAudioTrack.mockReset()
})

describe('VoiceClient checkpoint A+B protocol', () => {
  it('attaches one persistent track and completes with zero browser media controls', async () => {
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()
    const room = livekit.rooms[0]
    const track = { kind: 'audio' }
    room.emit('trackSubscribed', track, {}, { identity: 'agent-session-test-0001' })

    emitControl(room, 'session.ready', 1, { state: 'ready' })
    emitControl(room, 'turn.listening', 2)
    emitControl(room, 'stt.final', 3, { transcript: 'Вопрос.' })
    emitControl(room, 'turn.thinking', 4)
    emitControl(room, 'llm.visible', 5, {
      response: 'Ответ.', endpoint_to_first_visible_ms: 41,
    })
    emitControl(room, 'turn.speaking', 6, {
      server_streamed_output: true,
      endpoint_to_first_accepted_pcm_ms: 73,
    })
    emitControl(room, 'turn.completed', 7, { outcome: 'completed' }, true)

    expect(AudioPlaybackBoundary.prototype.setTrack).toHaveBeenCalledTimes(1)
    expect(room.localParticipant.publishData).not.toHaveBeenCalled()
    expect(observed.onControl).toHaveBeenCalledTimes(7)
    expect(observed.onConnection).not.toHaveBeenCalledWith('failed', expect.anything())
  })

  it('keeps diagnostics and conversation content in memory only', async () => {
    const storageWrite = vi.spyOn(Storage.prototype, 'setItem')
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()
    const room = livekit.rooms[0]

    emitControl(room, 'session.ready', 1, { state: 'ready' })
    emitControl(room, 'turn.listening', 2)
    emitControl(room, 'stt.final', 3, { transcript: 'Секретный вопрос.' })
    emitControl(room, 'turn.thinking', 4)
    emitControl(room, 'llm.visible', 5, { response: 'Приватный ответ.' })
    emitControl(room, 'turn.completed', 6, { outcome: 'completed' }, true)

    expect(storageWrite).not.toHaveBeenCalled()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
    const diagnosticOutput = JSON.stringify(
      vi.mocked(observed.onDiagnostic!).mock.calls.map(([record]) => record),
    )
    expect(diagnosticOutput).not.toContain('Секретный вопрос.')
    expect(diagnosticOutput).not.toContain('Приватный ответ.')
  })

  it('keeps an unrecognized STT failure in memory without degrading the session', async () => {
    const storageWrite = vi.spyOn(Storage.prototype, 'setItem')
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()
    const room = livekit.rooms[0]

    emitControl(room, 'session.ready', 1, { state: 'ready' })
    emitControl(room, 'turn.listening', 2)
    emitControl(room, 'turn.failed', 3, {
      outcome: 'failed', stage: 'stt', code: 'selected_stt_unavailable',
    }, true)

    expect(observed.onControl).toHaveBeenLastCalledWith(expect.objectContaining({
      type: 'turn.failed',
      payload: expect.objectContaining({ stage: 'stt' }),
    }))
    expect(observed.onConnection).not.toHaveBeenCalledWith('failed', expect.anything())
    expect(storageWrite).not.toHaveBeenCalled()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('keeps a visible prefix and session alive when TTS fails', async () => {
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()
    const room = livekit.rooms[0]

    emitControl(room, 'session.ready', 1, { state: 'ready' })
    emitControl(room, 'turn.listening', 2)
    emitControl(room, 'stt.final', 3, { transcript: 'Вопрос.' })
    emitControl(room, 'turn.thinking', 4)
    emitControl(room, 'llm.visible', 5, { response: 'Сохранённый текст.' })
    emitControl(room, 'turn.failed', 6, {
      outcome: 'failed', stage: 'tts', code: 'selected_tts_unavailable',
    }, true)

    expect(observed.onControl).toHaveBeenLastCalledWith(expect.objectContaining({
      type: 'turn.failed',
      payload: expect.objectContaining({ stage: 'tts' }),
    }))
    expect(observed.onConnection).not.toHaveBeenCalledWith('failed', expect.anything())
    expect(observed.onDiagnostic).toHaveBeenCalledWith(expect.objectContaining({
      serverControlType: 'turn.failed',
      failureStage: 'tts',
      failureCode: 'selected_tts_unavailable',
    }))
  })

  it('publishes only the reset control after a transport reconnect', async () => {
    const client = new VoiceClient(document.createElement('div'), callbacks())
    await client.start()
    const room = livekit.rooms[0]
    room.emit('reconnecting')
    room.emit('reconnected')
    await Promise.resolve()

    expect(room.localParticipant.publishData).toHaveBeenCalledTimes(1)
    const [bytes, options] = room.localParticipant.publishData.mock.calls[0]
    expect(JSON.parse(new TextDecoder().decode(bytes))).toMatchObject({
      type: 'client.reconnected',
      stream_epoch: 1,
    })
    expect(options.topic).toBe('voice-agent.client-control.v1')
  })
})
