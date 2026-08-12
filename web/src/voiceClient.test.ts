import { afterEach, describe, expect, it, vi } from 'vitest'

const livekit = vi.hoisted(() => {
  const rooms: FakeRoom[] = []
  const createLocalAudioTrack = vi.fn()
  let includeAgent = true

  class FakeRoom {
    connect = vi.fn().mockResolvedValue(undefined)
    disconnect = vi.fn().mockResolvedValue(undefined)
    startAudio = vi.fn().mockResolvedValue(undefined)
    localParticipant = {
      publishTrack: vi.fn().mockResolvedValue(undefined),
      publishData: vi.fn().mockResolvedValue(undefined),
    }
    remoteParticipants = new Map(
      includeAgent
        ? [['agent-session-test-0001', { identity: 'agent-session-test-0001' }]]
        : [],
    )
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

  return {
    FakeRoom,
    createLocalAudioTrack,
    rooms,
    setIncludeAgent(value: boolean) { includeAgent = value },
  }
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
  const payload = options.payload ?? {}
  const correlatedPayload = (
    (type === 'turn.interrupted' || type === 'turn.failed')
    && payload.media_generation === undefined
  ) ? { ...payload, media_generation: sequence } : payload
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
      payload: correlatedPayload,
    })),
    { identity: 'agent-session-test-0001' },
    undefined,
    'voice-agent.control.v1',
  )
}

function emitSpeakingBoundary(
  room: { emit(event: string, ...args: any[]): void },
  publicationId: string,
): void {
  for (const [index, type] of [
    'session.ready', 'turn.listening', 'turn.transcribing', 'stt.final',
    'turn.thinking', 'llm.final', 'turn.speaking',
  ].entries()) {
    emitControl(room, type, index + 1, {
      payload: type === 'turn.speaking' ? {
        state: 'awaiting_media',
        media_generation: 1,
        media_publication_id: publicationId,
        media_ready_timeout_ms: 3_000,
        media_ready_ack_deadline_ms: 2_750,
      } : {},
    })
  }
}

afterEach(() => {
  vi.useRealTimers()
  livekit.rooms.length = 0
  livekit.createLocalAudioTrack.mockReset()
  livekit.setIncludeAgent(true)
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

  it('waits for a fresh per-turn track and its correlated render boundary', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(capabilityResponse()))
    livekit.createLocalAudioTrack.mockResolvedValue({ stop: vi.fn() })
    const client = new VoiceClient(document.createElement('div'), callbacks())
    await client.start()
    const room = livekit.rooms[0]
    const element = document.createElement('audio')
    element.play = vi.fn().mockResolvedValue(undefined)
    element.pause = vi.fn()
    element.load = vi.fn()
    let renderObserver = (
      _sampleCount: number, _sampleRate: number, _signal?: boolean,
    ): void => { throw new Error('render observer was not installed') }
    const turnTrack = {
      kind: 'audio',
      attach: vi.fn().mockReturnValue(element),
      detach: vi.fn().mockReturnValue([]),
      observeRenderedSamples: vi.fn((observer: (samples: number, rate: number, signal?: boolean) => void) => {
        renderObserver = observer
        return vi.fn()
      }),
    }
    const publication = { trackSid: 'publication-turn-1' }

    for (const [index, type] of [
      'session.ready', 'turn.listening', 'turn.transcribing', 'stt.final',
      'turn.thinking', 'llm.final', 'turn.speaking',
    ].entries()) {
      emitControl(room, type, index + 1, {
        payload: type === 'turn.speaking' ? {
          state: 'awaiting_media',
          media_generation: 1,
          media_publication_id: 'publication-turn-1',
          media_ready_timeout_ms: 3_000,
          media_ready_ack_deadline_ms: 2_750,
        } : {},
      })
    }
    await vi.waitFor(() => expect(room.localParticipant.publishData).toHaveBeenCalledOnce())
    expect(JSON.parse(new TextDecoder().decode(
      room.localParticipant.publishData.mock.calls[0][0] as Uint8Array,
    ))).toMatchObject({
      media_generation: 1,
      wait_kind: 'media-ready',
      type: 'client.wait-started',
    })
    expect(turnTrack.attach).not.toHaveBeenCalled()

    room.emit('trackSubscribed', turnTrack, publication, {
      identity: 'agent-session-test-0001',
    })
    await vi.waitFor(() => expect(room.localParticipant.publishData).toHaveBeenCalledTimes(2))
    expect(turnTrack.attach).toHaveBeenCalledOnce()
    expect(JSON.parse(new TextDecoder().decode(
      room.localParticipant.publishData.mock.calls[1][0] as Uint8Array,
    ))).toMatchObject({ media_generation: 1, type: 'client.media-ready' })

    emitControl(room, 'turn.playout-ready', 8, {
      payload: {
        state: 'awaiting_client_playout_boundary',
        ack_timeout_ms: 3_000,
        ack_deadline_ms: 2_750,
        media_generation: 2,
        completed_publication_id: 'publication-turn-1',
        final_sample_count: 320,
        sample_rate_hz: 16_000,
      },
    })
    await vi.waitFor(() => expect(room.localParticipant.publishData).toHaveBeenCalledTimes(3))
    expect(JSON.parse(new TextDecoder().decode(
      room.localParticipant.publishData.mock.calls[2][0] as Uint8Array,
    ))).toMatchObject({
      media_generation: 2,
      wait_kind: 'playout',
      type: 'client.wait-started',
    })
    renderObserver(10_000, 16_000, false)
    renderObserver(319, 16_000, true)
    await Promise.resolve()
    expect(room.localParticipant.publishData).toHaveBeenCalledTimes(3)
    renderObserver(1, 16_000, true)
    await vi.waitFor(() => expect(room.localParticipant.publishData).toHaveBeenCalledTimes(4))
    expect(JSON.parse(new TextDecoder().decode(
      room.localParticipant.publishData.mock.calls[3][0] as Uint8Array,
    ))).toMatchObject({
      media_generation: 2,
      completed_publication_id: 'publication-turn-1',
      type: 'client.playout-completed',
    })
    expect(turnTrack.detach).toHaveBeenCalledWith(element)
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
    room.emit('trackSubscribed', oldTrack, { trackSid: 'publication-old' }, { identity: 'agent-session-test-0001' })
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
    room.emit('trackSubscribed', replacementTrack, { trackSid: 'publication-reconnected' }, { identity: 'agent-session-test-0001' })
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
          state: 'awaiting_media',
          stale_media_discarded: true,
          conversation_context_reset: true,
          media_generation: 1,
          media_ready_timeout_ms: 3_000,
          media_ready_ack_deadline_ms: 2_750,
          media_publication_id: 'publication-reconnected',
          interrupted_turn_id: null,
        },
      })),
      { identity: 'agent-session-test-0001' },
      undefined,
      'voice-agent.control.v1',
    )

    expect(replacementTrack.attach).toHaveBeenCalledOnce()
    await vi.waitFor(() => expect(room.localParticipant.publishData).toHaveBeenCalledTimes(3))
    const waitPayload = room.localParticipant.publishData.mock.calls[1][0] as Uint8Array
    expect(JSON.parse(new TextDecoder().decode(waitPayload))).toMatchObject({
      turn_id: 'session',
      stream_epoch: 2,
      media_generation: 1,
      wait_kind: 'media-ready',
      type: 'client.wait-started',
    })
    const mediaReadyPayload = room.localParticipant.publishData.mock.calls[2][0] as Uint8Array
    expect(JSON.parse(new TextDecoder().decode(mediaReadyPayload))).toMatchObject({
      turn_id: 'session',
      stream_epoch: 2,
      media_generation: 1,
      type: 'client.media-ready',
    })
    emitControl(room, 'session.ready', 2, {
      streamEpoch: 2,
      payload: { state: 'ready', media_generation: 1 },
    })
    await client.stop()
  })

  it('uses the server-provided reconnect acknowledgement deadline', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(capabilityResponse()))
    livekit.createLocalAudioTrack.mockResolvedValue({ stop: vi.fn() })
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()
    const room = livekit.rooms[0]

    room.emit('reconnecting')
    room.emit('reconnected')
    await Promise.resolve()
    emitControl(room, 'session.reconnected', 1, {
      streamEpoch: 2,
      payload: {
        state: 'awaiting_media',
        stale_media_discarded: true,
        conversation_context_reset: true,
        media_generation: 1,
        media_ready_timeout_ms: 3_000,
        media_ready_ack_deadline_ms: 2_750,
        media_publication_id: 'publication-missing',
        interrupted_turn_id: null,
      },
    })

    await vi.advanceTimersByTimeAsync(2_749)
    expect(observed.onConnection).not.toHaveBeenLastCalledWith(
      'failed', expect.any(String),
    )
    await vi.advanceTimersByTimeAsync(2)
    expect(observed.onConnection).toHaveBeenLastCalledWith(
      'failed', 'Сервер не подтвердил готовность аудиопотока',
    )
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

  it('invalidates failed turn media before accepting a fresh turn publication', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(capabilityResponse()))
    livekit.createLocalAudioTrack.mockResolvedValue({ stop: vi.fn() })
    const client = new VoiceClient(document.createElement('div'), callbacks())
    await client.start()
    const room = livekit.rooms[0]
    const element = document.createElement('audio')
    element.play = vi.fn().mockResolvedValue(undefined)
    element.pause = vi.fn()
    element.load = vi.fn()
    const staleTrack = {
      kind: 'audio',
      attach: vi.fn().mockReturnValue(element),
      detach: vi.fn().mockReturnValue([]),
      observeRenderedSamples: vi.fn().mockReturnValue(vi.fn()),
    }
    for (const [index, type] of [
      'session.ready', 'turn.listening', 'turn.transcribing', 'stt.final',
      'turn.thinking', 'llm.final', 'turn.speaking',
    ].entries()) {
      emitControl(room, type, index + 1, {
        payload: type === 'turn.speaking' ? {
          state: 'awaiting_media', media_generation: 1,
          media_publication_id: 'publication-stale',
          media_ready_timeout_ms: 3_000, media_ready_ack_deadline_ms: 2_750,
        } : {},
      })
    }
    room.emit('trackSubscribed', staleTrack, { trackSid: 'publication-stale' }, {
      identity: 'agent-session-test-0001',
    })
    await vi.waitFor(() => expect(room.localParticipant.publishData).toHaveBeenCalledTimes(2))

    emitControl(room, 'turn.failed', 8, {
      terminal: true,
      payload: {
        stage: 'publication', code: 'audio_playout_exception', media_generation: 2,
      },
    })
    await vi.waitFor(() => expect(room.localParticipant.publishData).toHaveBeenCalledTimes(4))

    expect(staleTrack.detach).toHaveBeenCalledWith(element)
    const controls = room.localParticipant.publishData.mock.calls.map(([payload]) => (
      JSON.parse(new TextDecoder().decode(payload as Uint8Array))
    ))
    expect(controls.slice(2).map((control) => control.type)).toEqual([
      'client.wait-started', 'client.media-ready',
    ])
    const unexpected = { kind: 'audio', attach: vi.fn(), detach: vi.fn() }
    room.emit('trackSubscribed', unexpected, { trackSid: 'publication-unexpected' }, {
      identity: 'agent-session-test-0001',
    })
    expect(unexpected.attach).not.toHaveBeenCalled()
    await client.stop()
  })

  it('fails closed without desynchronizing control state when media invalidation throws', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(capabilityResponse()))
    livekit.createLocalAudioTrack.mockResolvedValue({ stop: vi.fn() })
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()
    const room = livekit.rooms[0]
    const element = document.createElement('audio')
    element.play = vi.fn().mockResolvedValue(undefined)
    element.pause = vi.fn()
    element.load = vi.fn()
    const track = {
      kind: 'audio',
      attach: vi.fn().mockReturnValue(element),
      detach: vi.fn()
        .mockImplementationOnce(() => { throw new Error('detach failed') })
        .mockReturnValue([]),
    }
    for (const [index, type] of [
      'session.ready', 'turn.listening', 'turn.transcribing', 'stt.final',
      'turn.thinking', 'llm.final', 'turn.speaking',
    ].entries()) {
      emitControl(room, type, index + 1, {
        payload: type === 'turn.speaking' ? {
          state: 'awaiting_media', media_generation: 1,
          media_publication_id: 'publication-failing',
          media_ready_timeout_ms: 3_000, media_ready_ack_deadline_ms: 2_750,
        } : {},
      })
    }
    room.emit('trackSubscribed', track, { trackSid: 'publication-failing' }, {
      identity: 'agent-session-test-0001',
    })

    expect(() => emitControl(room, 'turn.failed', 8, {
      terminal: true, payload: { media_generation: 2 },
    })).not.toThrow()
    expect(observed.onControl).toHaveBeenLastCalledWith(
      expect.objectContaining({ type: 'turn.failed' }),
    )
    await vi.waitFor(() => expect(observed.onConnection).toHaveBeenLastCalledWith(
      'failed',
      'Не удалось остановить устаревшее воспроизведение',
    ))
    expect(track.detach).toHaveBeenCalledTimes(2)
    expect(room.disconnect).toHaveBeenCalledOnce()
  })

  it('coalesces consecutive invalidations onto the latest media generation', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(capabilityResponse()))
    livekit.createLocalAudioTrack.mockResolvedValue({ stop: vi.fn() })
    const client = new VoiceClient(document.createElement('div'), callbacks())
    await client.start()
    const room = livekit.rooms[0]
    emitControl(room, 'session.ready', 1)
    emitControl(room, 'turn.listening', 2)
    emitControl(room, 'turn.failed', 3, {
      terminal: true, payload: { media_generation: 1 },
    })
    emitControl(room, 'turn.listening', 4, { turnId: 'turn-00000002' })
    emitControl(room, 'turn.failed', 5, {
      turnId: 'turn-00000002', terminal: true,
      payload: { media_generation: 2 },
    })

    await vi.waitFor(() => {
      const last = room.localParticipant.publishData.mock.calls.at(-1)?.[0] as Uint8Array
      expect(JSON.parse(new TextDecoder().decode(last)).type).toBe('client.media-ready')
    })
    const controls = room.localParticipant.publishData.mock.calls.map(([payload]) => (
      JSON.parse(new TextDecoder().decode(payload as Uint8Array))
    ))
    expect(controls.at(-1)).toMatchObject({
      turn_id: 'turn-00000002',
      media_generation: 2,
      type: 'client.media-ready',
    })
    const staleTrack = { kind: 'audio', attach: vi.fn(), detach: vi.fn() }
    room.emit('trackSubscribed', staleTrack, { trackSid: 'publication-stale' }, {
      identity: 'agent-session-test-0001',
    })
    expect(staleTrack.attach).not.toHaveBeenCalled()
    await client.stop()
  })

  it('rejects a room whose agent timed out before the browser joined', async () => {
    livekit.setIncludeAgent(false)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(capabilityResponse()))
    livekit.createLocalAudioTrack.mockResolvedValue({ stop: vi.fn() })
    const client = new VoiceClient(document.createElement('div'), callbacks())

    await expect(client.start()).rejects.toThrow('agent is unavailable')
    await client.stop()

    const room = livekit.rooms[0]
    expect(room.disconnect).toHaveBeenCalledOnce()
    expect(livekit.createLocalAudioTrack).not.toHaveBeenCalled()
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

  it('retains failed cleanup handles and releases each one on retry', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(capabilityResponse()))
    const microphone = {
      stop: vi.fn()
        .mockImplementationOnce(() => { throw new Error('stop failed') })
        .mockImplementation(() => undefined),
    }
    livekit.createLocalAudioTrack.mockResolvedValue(microphone)
    const observed = callbacks()
    const container = document.createElement('div')
    const client = new VoiceClient(container, observed)
    await client.start()
    const room = livekit.rooms[0]
    room.disconnect.mockRejectedValueOnce(new Error('disconnect failed'))
    const element = document.createElement('audio')
    element.play = vi.fn().mockResolvedValue(undefined)
    element.pause = vi.fn()
    element.load = vi.fn()
    const remoteTrack = {
      kind: 'audio',
      attach: vi.fn().mockReturnValue(element),
      detach: vi.fn()
        .mockImplementationOnce(() => { throw new Error('detach failed') })
        .mockReturnValue([]),
    }
    emitSpeakingBoundary(room, 'publication-cleanup')
    room.emit('trackSubscribed', remoteTrack, { trackSid: 'publication-cleanup' }, {
      identity: 'agent-session-test-0001',
    })

    room.emit('participantDisconnected', { identity: 'agent-session-test-0001' })

    await vi.waitFor(() => expect(observed.onConnection).toHaveBeenLastCalledWith(
      'failed',
      'Агент голосовой сессии отключился (не удалось полностью освободить транспорт)',
    ))
    expect(container.childElementCount).toBe(0)
    expect(element.pause).toHaveBeenCalledOnce()
    expect(element.load).toHaveBeenCalledOnce()

    await expect(client.stop()).resolves.toBeUndefined()
    expect(microphone.stop).toHaveBeenCalledTimes(2)
    expect(remoteTrack.detach).toHaveBeenCalledTimes(2)
    expect(room.disconnect).toHaveBeenCalledTimes(2)
    expect(observed.onConnection).toHaveBeenLastCalledWith('closed')
  })

  it('reports manual cleanup failure instead of a false closed state', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(capabilityResponse()))
    const microphone = {
      stop: vi.fn()
        .mockImplementationOnce(() => { throw new Error('stop failed') })
        .mockImplementation(() => undefined),
    }
    livekit.createLocalAudioTrack.mockResolvedValue(microphone)
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()
    const room = livekit.rooms[0]
    room.disconnect.mockRejectedValueOnce(new Error('disconnect failed'))

    await expect(client.stop()).rejects.toThrow('resource cleanup failed')
    expect(observed.onConnection).toHaveBeenLastCalledWith(
      'failed',
      'Не удалось полностью освободить ресурсы голосовой сессии',
    )

    await expect(client.stop()).resolves.toBeUndefined()
    expect(microphone.stop).toHaveBeenCalledTimes(2)
    expect(room.disconnect).toHaveBeenCalledTimes(2)
    expect(observed.onConnection).toHaveBeenLastCalledWith('closed')
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
    emitSpeakingBoundary(room, 'publication-degraded')
    room.emit('trackSubscribed', remoteTrack, { trackSid: 'publication-degraded' }, {
      identity: 'agent-session-test-0001',
    })
    emitControl(room, 'session.degraded', 8, {
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
