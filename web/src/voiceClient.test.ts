import { afterEach, describe, expect, it, vi } from 'vitest'

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

import { VoiceClient, type VoiceClientCallbacks } from './voiceClient'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((accept) => {
    resolve = accept
  })
  return { promise, resolve }
}

function capabilityResponse(): Response {
  return {
    ok: true,
    json: vi.fn().mockResolvedValue({
      session_id: 'session-test-0001',
      stream_epoch: 1,
      livekit_url: 'wss://voice.test.ts.net:7443',
      token: 'room-token-long-enough',
      expires_in_seconds: 30,
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
  }
}

function emitControl(
  room: { emit(event: string, ...args: any[]): void },
  type: string,
  sequence: number,
  options: {
    turnId?: string
    terminal?: boolean
    payload?: Record<string, unknown>
    streamEpoch?: number
  } = {},
): void {
  room.emit(
    'dataReceived',
    new TextEncoder().encode(JSON.stringify({
      schema_version: 'voice-agent.realtime-control.v1',
      session_id: 'session-test-0001',
      turn_id: options.turnId ?? (type.startsWith('session.') ? 'session' : 'turn-00000001'),
      stream_epoch: options.streamEpoch ?? 1,
      sequence,
      type,
      terminal: options.terminal ?? false,
      payload: options.payload ?? {},
    })),
    { identity: 'agent-session-test-0001' },
    undefined,
    'voice-agent.control.v1',
  )
}

afterEach(() => {
  vi.useRealTimers()
  livekit.rooms.length = 0
  livekit.createLocalAudioTrack.mockReset()
  vi.unstubAllGlobals()
})

describe('VoiceClient startup cancellation', () => {
  it('does not connect after a late capability response', async () => {
    const response = deferred<Response>()
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(response.promise))
    const client = new VoiceClient(document.createElement('div'), callbacks())

    const start = client.start()
    await client.stop()
    response.resolve(capabilityResponse())

    await expect(start).rejects.toThrow('cancelled')
    expect(livekit.rooms).toHaveLength(0)
    expect(livekit.createLocalAudioTrack).not.toHaveBeenCalled()
  })

  it('stops a microphone created after disconnect and disconnects again', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(capabilityResponse()))
    const microphone = {
      stop: vi.fn(),
    }
    const track = deferred<typeof microphone>()
    livekit.createLocalAudioTrack.mockReturnValue(track.promise)
    const client = new VoiceClient(document.createElement('div'), callbacks())

    const start = client.start()
    await vi.waitFor(() => expect(livekit.createLocalAudioTrack).toHaveBeenCalledOnce())
    const room = livekit.rooms[0]
    await client.stop()
    track.resolve(microphone)

    await expect(start).rejects.toThrow('cancelled')
    expect(microphone.stop).toHaveBeenCalledOnce()
    expect(room.disconnect).toHaveBeenCalledTimes(2)
    expect(room.localParticipant.publishTrack).not.toHaveBeenCalled()
  })

  it('publishes playout acknowledgement after the media clock drain boundary', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(capabilityResponse()))
    livekit.createLocalAudioTrack.mockResolvedValue({ stop: vi.fn() })
    const client = new VoiceClient(document.createElement('div'), callbacks())
    await client.start()
    const room = livekit.rooms[0]
    const remoteElement = document.createElement('audio')
    remoteElement.play = vi.fn().mockResolvedValue(undefined)
    remoteElement.pause = vi.fn()
    remoteElement.load = vi.fn()
    room.emit(
      'trackSubscribed',
      {
        kind: 'audio',
        attach: vi.fn().mockReturnValue(remoteElement),
        detach: vi.fn().mockReturnValue([]),
      },
      {},
      { identity: 'agent-session-test-0001' },
    )
    const types = [
      'session.ready', 'turn.listening', 'turn.transcribing', 'stt.final',
      'turn.thinking', 'llm.final', 'turn.speaking', 'turn.playout-ready',
    ]
    for (const [index, type] of types.entries()) {
      const sessionEvent = type.startsWith('session.')
      const payload = type === 'turn.playout-ready' ? { drain_bound_ms: 250 } : {}
      room.emit(
        'dataReceived',
        new TextEncoder().encode(JSON.stringify({
          schema_version: 'voice-agent.realtime-control.v1',
          session_id: 'session-test-0001',
          turn_id: sessionEvent ? 'session' : 'turn-00000001',
          stream_epoch: 1,
          sequence: index + 1,
          type,
          terminal: false,
          payload,
        })),
        { identity: 'agent-session-test-0001' },
        undefined,
        'voice-agent.control.v1',
      )
    }
    Object.defineProperty(remoteElement, 'currentTime', { value: 0.3, configurable: true })
    await vi.advanceTimersByTimeAsync(20)

    expect(room.localParticipant.publishData).toHaveBeenCalledOnce()
    const encoded = room.localParticipant.publishData.mock.calls[0][0] as Uint8Array
    expect(JSON.parse(new TextDecoder().decode(encoded))).toMatchObject({
      turn_id: 'turn-00000001',
      stream_epoch: 1,
      type: 'client.playout-completed',
    })
  })

  it('keeps replacement audio detached and coalesces reconnects until epoch acknowledgement', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(capabilityResponse()))
    livekit.createLocalAudioTrack.mockResolvedValue({ stop: vi.fn() })
    const client = new VoiceClient(document.createElement('div'), callbacks())
    await client.start()
    const room = livekit.rooms[0]
    const oldElement = document.createElement('audio')
    oldElement.play = vi.fn().mockResolvedValue(undefined)
    oldElement.pause = vi.fn()
    oldElement.load = vi.fn()
    const oldTrack = {
      kind: 'audio',
      attach: vi.fn().mockReturnValue(oldElement),
      detach: vi.fn().mockReturnValue([]),
    }
    room.emit('trackSubscribed', oldTrack, {}, { identity: 'agent-session-test-0001' })
    const replacementElement = document.createElement('audio')
    replacementElement.play = vi.fn().mockResolvedValue(undefined)
    replacementElement.pause = vi.fn()
    replacementElement.load = vi.fn()
    const replacementTrack = {
      kind: 'audio',
      attach: vi.fn().mockReturnValue(replacementElement),
      detach: vi.fn().mockReturnValue([]),
    }

    room.emit('reconnecting')
    room.emit('reconnected')
    room.emit('reconnected')
    room.emit('trackSubscribed', replacementTrack, {}, { identity: 'agent-session-test-0001' })
    await Promise.resolve()

    expect(room.localParticipant.publishData).toHaveBeenCalledOnce()
    const reconnectPayload = room.localParticipant.publishData.mock.calls[0][0] as Uint8Array
    expect(JSON.parse(new TextDecoder().decode(reconnectPayload))).toMatchObject({
      stream_epoch: 1,
      type: 'client.reconnected',
    })
    expect(replacementTrack.attach).not.toHaveBeenCalled()

    room.emit(
      'dataReceived',
      new TextEncoder().encode(JSON.stringify({
        schema_version: 'voice-agent.realtime-control.v1',
        session_id: 'session-test-0001',
        turn_id: 'session',
        stream_epoch: 2,
        sequence: 1,
        type: 'session.reconnected',
        terminal: false,
        payload: {
          state: 'ready',
          stale_media_discarded: true,
          conversation_context_reset: true,
        },
      })),
      { identity: 'agent-session-test-0001' },
      undefined,
      'voice-agent.control.v1',
    )

    expect(replacementTrack.attach).toHaveBeenCalledOnce()
    await client.stop()
  })

  it('fails closed when reconnect receives no server epoch acknowledgement', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(capabilityResponse()))
    const microphone = { stop: vi.fn() }
    livekit.createLocalAudioTrack.mockResolvedValue(microphone)
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()
    const room = livekit.rooms[0]

    room.emit('reconnecting')
    room.emit('reconnected')
    await Promise.resolve()
    expect(room.localParticipant.publishData).toHaveBeenCalledOnce()
    await vi.advanceTimersByTimeAsync(5_001)

    expect(microphone.stop).toHaveBeenCalledOnce()
    expect(room.disconnect).toHaveBeenCalledOnce()
    expect(observed.onConnection).toHaveBeenLastCalledWith(
      'failed',
      'Сервер не подтвердил восстановление сессии',
    )
  })

  it('requires a fresh media subscription when a partially published turn fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(capabilityResponse()))
    livekit.createLocalAudioTrack.mockResolvedValue({ stop: vi.fn() })
    const client = new VoiceClient(document.createElement('div'), callbacks())
    await client.start()
    const room = livekit.rooms[0]
    const firstElement = document.createElement('audio')
    firstElement.play = vi.fn().mockResolvedValue(undefined)
    firstElement.pause = vi.fn()
    firstElement.load = vi.fn()
    const staleTrack = {
      kind: 'audio',
      attach: vi.fn().mockReturnValue(firstElement),
      detach: vi.fn().mockReturnValue([]),
    }
    const publication = { setSubscribed: vi.fn() }
    room.emit('trackSubscribed', staleTrack, publication, { identity: 'agent-session-test-0001' })
    emitControl(room, 'session.ready', 1)
    emitControl(room, 'turn.listening', 2)
    emitControl(room, 'turn.failed', 3, {
      terminal: true,
      payload: { stage: 'publication', code: 'audio_playout_exception' },
    })

    expect(staleTrack.detach).toHaveBeenCalledWith(firstElement)
    expect(staleTrack.attach).toHaveBeenCalledOnce()
    expect(publication.setSubscribed).toHaveBeenLastCalledWith(false)

    room.emit('trackUnsubscribed', staleTrack, publication, {
      identity: 'agent-session-test-0001',
    })
    expect(publication.setSubscribed).toHaveBeenLastCalledWith(true)

    const freshElement = document.createElement('audio')
    freshElement.play = vi.fn().mockResolvedValue(undefined)
    freshElement.pause = vi.fn()
    freshElement.load = vi.fn()
    const freshTrack = {
      kind: 'audio',
      attach: vi.fn().mockReturnValue(freshElement),
      detach: vi.fn().mockReturnValue([]),
    }
    room.emit('trackSubscribed', staleTrack, publication, { identity: 'agent-session-test-0001' })
    expect(staleTrack.attach).toHaveBeenCalledOnce()
    room.emit('trackSubscribed', freshTrack, publication, { identity: 'agent-session-test-0001' })
    expect(freshTrack.attach).toHaveBeenCalledOnce()
    await client.stop()
  })

  it('fails closed when the agent participant leaves before readiness', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(capabilityResponse()))
    const microphone = { stop: vi.fn() }
    livekit.createLocalAudioTrack.mockResolvedValue(microphone)
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()
    const room = livekit.rooms[0]

    room.emit('participantDisconnected', { identity: 'agent-session-test-0001' })

    await vi.waitFor(() => expect(observed.onConnection).toHaveBeenLastCalledWith(
      'failed',
      'Агент голосовой сессии отключился',
    ))
    expect(microphone.stop).toHaveBeenCalledOnce()
    expect(room.disconnect).toHaveBeenCalledOnce()
  })

  it('bounds the wait for initial session readiness', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(capabilityResponse()))
    const microphone = { stop: vi.fn() }
    livekit.createLocalAudioTrack.mockResolvedValue(microphone)
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()
    const room = livekit.rooms[0]

    await vi.advanceTimersByTimeAsync(30_001)

    expect(microphone.stop).toHaveBeenCalledOnce()
    expect(room.disconnect).toHaveBeenCalledOnce()
    expect(observed.onConnection).toHaveBeenLastCalledWith(
      'failed',
      'Сервер не подтвердил готовность голосовой сессии',
    )
  })

  it('reports failure and remains stoppable when room cleanup rejects', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(capabilityResponse()))
    const microphone = { stop: vi.fn() }
    livekit.createLocalAudioTrack.mockResolvedValue(microphone)
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()
    const room = livekit.rooms[0]
    room.disconnect.mockRejectedValueOnce(new Error('disconnect failed'))

    room.emit('participantDisconnected', { identity: 'agent-session-test-0001' })

    await vi.waitFor(() => expect(observed.onConnection).toHaveBeenLastCalledWith(
      'failed',
      'Агент голосовой сессии отключился (не удалось полностью освободить транспорт)',
    ))
    await expect(client.stop()).resolves.toBeUndefined()
    expect(microphone.stop).toHaveBeenCalledOnce()
  })

  it('releases microphone, playback, and room after terminal degradation', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(capabilityResponse()))
    const microphone = { stop: vi.fn() }
    livekit.createLocalAudioTrack.mockResolvedValue(microphone)
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()
    const room = livekit.rooms[0]
    const element = document.createElement('audio')
    element.play = vi.fn().mockResolvedValue(undefined)
    element.pause = vi.fn()
    element.load = vi.fn()
    const remoteTrack = {
      kind: 'audio',
      attach: vi.fn().mockReturnValue(element),
      detach: vi.fn().mockReturnValue([]),
    }
    room.emit('trackSubscribed', remoteTrack, {}, { identity: 'agent-session-test-0001' })
    emitControl(room, 'session.ready', 1)
    emitControl(room, 'session.degraded', 2, {
      payload: { stage: 'input', code: 'microphone_stream_ended' },
    })

    await vi.waitFor(() => expect(observed.onConnection).toHaveBeenLastCalledWith(
      'failed',
      'Голосовая сессия остановлена (input/microphone_stream_ended)',
    ))
    expect(room.disconnect).toHaveBeenCalledOnce()
    expect(microphone.stop).toHaveBeenCalledOnce()
    expect(remoteTrack.detach).toHaveBeenCalledWith(element)
    expect(observed.onControl).toHaveBeenLastCalledWith(
      expect.objectContaining({ type: 'session.degraded' }),
    )
  })
})
