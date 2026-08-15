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
    SignalReconnecting: 'signalReconnecting',
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
import { ACTIVE_LLM_MODEL_IDENTITY } from './state'
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
      control_version: 'voice-agent.realtime-control.v2',
      llm_profile: {
        provider_mode: 'local', model_identity: ACTIVE_LLM_MODEL_IDENTITY,
      },
      tts_profile: {
        profile: 'silero-kseniya', backend: 'silero', speaker: 'kseniya',
        output_sample_rate_hz: 48_000, native_sample_rate_hz: 48_000,
        license: 'CC-BY-NC-SA-4.0', private_noncommercial_only: true,
      },
    }),
  } as unknown as Response
}

function mediaReadyPayload() {
  return {
    server_media_publication_id: 'publication-test',
    audio: {
      encoding: 'pcm_s16le', sample_rate_hz: 48_000, channels: 1, sample_width_bytes: 2,
    },
    tts: {
      profile: 'silero-kseniya', backend: 'silero', speaker: 'kseniya',
      output_sample_rate_hz: 48_000, native_sample_rate_hz: 48_000,
      license: 'CC-BY-NC-SA-4.0', private_noncommercial_only: true,
    },
  }
}

function callbacks(): VoiceClientCallbacks {
  return {
    onSession: vi.fn(),
    onConnection: vi.fn(),
    onControl: vi.fn(),
    onDrop: vi.fn(),
    onAudioBlocked: vi.fn(),
    onSpeechEnvelope: vi.fn(),
    onSpeechEnvelopeStatus: vi.fn(),
    onMicrophoneLifecycle: vi.fn(),
    onMicrophoneState: vi.fn(),
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
  turnGeneration = 1,
  mediaGeneration = turnGeneration,
  turnId = 'turn-00000001',
): void {
  room.emit(
    'dataReceived',
    new TextEncoder().encode(JSON.stringify({
      schema_version: 'voice-agent.realtime-control.v2',
      session_id: 'session-test-0001',
      turn_id: type.startsWith('session.') ? 'session' : turnId,
      stream_epoch: streamEpoch,
      turn_generation: type.startsWith('session.') ? 0 : turnGeneration,
      request_id: type.startsWith('session.') ? 'session' : `request-${turnGeneration.toString().padStart(8, '0')}`,
      media_generation: type.startsWith('session.') ? 0 : mediaGeneration,
      sequence,
      type,
      terminal,
      payload,
    })),
    { identity: 'agent-session-test-0001' },
    undefined,
    'voice-agent.control.v2',
  )
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(capabilityResponse()))
  livekit.createLocalAudioTrack.mockResolvedValue({
    isMuted: false,
    mute: vi.fn().mockResolvedValue(undefined),
    unmute: vi.fn().mockResolvedValue(undefined),
    stop: vi.fn(),
  })
  vi.spyOn(AudioPlaybackBoundary.prototype, 'setTrack').mockImplementation(() => undefined)
  vi.spyOn(AudioPlaybackBoundary.prototype, 'reset').mockImplementation(() => undefined)
  vi.spyOn(AudioPlaybackBoundary.prototype, 'finishGeneration').mockImplementation(() => true)
  vi.spyOn(AudioPlaybackBoundary.prototype, 'suspend').mockImplementation(() => true)
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
  it('attaches only the current announced publication generation and sends no media ACK', async () => {
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()
    const room = livekit.rooms[0]
    const track = { kind: 'audio' }
    room.emit('trackSubscribed', track, { trackSid: 'publication-test' }, { identity: 'agent-session-test-0001' })
    expect(AudioPlaybackBoundary.prototype.setTrack).not.toHaveBeenCalled()

    emitControl(room, 'session.ready', 1, { state: 'ready' })
    emitControl(room, 'turn.listening', 2)
    emitControl(room, 'turn.media-ready', 3, mediaReadyPayload())
    emitControl(room, 'stt.final', 4, { transcript: 'Вопрос.' })
    emitControl(room, 'turn.thinking', 5)
    emitControl(room, 'llm.visible', 6, {
      response: 'Ответ.', endpoint_to_first_visible_ms: 41,
    })
    emitControl(room, 'turn.speaking', 7, {
      server_streamed_output: true,
      endpoint_to_first_accepted_pcm_ms: 73,
    })
    emitControl(room, 'turn.completed', 8, { outcome: 'completed' }, true)

    expect(AudioPlaybackBoundary.prototype.setTrack).toHaveBeenCalledTimes(1)
    expect(AudioPlaybackBoundary.prototype.finishGeneration).toHaveBeenCalledWith(1)
    expect(room.localParticipant.publishData).not.toHaveBeenCalled()
    expect(observed.onControl).toHaveBeenCalledTimes(8)
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
    emitControl(room, 'turn.media-ready', 3, mediaReadyPayload())
    emitControl(room, 'stt.final', 4, { transcript: 'Секретный вопрос.' })
    emitControl(room, 'turn.thinking', 5)
    emitControl(room, 'llm.visible', 6, { response: 'Приватный ответ.' })
    emitControl(room, 'turn.completed', 7, { outcome: 'completed' }, true)

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
    emitControl(room, 'turn.media-ready', 3, mediaReadyPayload())
    emitControl(room, 'turn.failed', 4, {
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
    emitControl(room, 'turn.media-ready', 3, mediaReadyPayload())
    emitControl(room, 'stt.final', 4, { transcript: 'Вопрос.' })
    emitControl(room, 'turn.thinking', 5)
    emitControl(room, 'llm.visible', 6, { response: 'Сохранённый текст.' })
    emitControl(room, 'turn.failed', 7, {
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

  it('stops only matching current generations across rapid valid and invalid interrupts', async () => {
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()
    const room = livekit.rooms[0]
    const track = { kind: 'audio' }
    room.emit(
      'trackSubscribed', track, { trackSid: 'publication-test' },
      { identity: 'agent-session-test-0001' },
    )
    emitControl(room, 'session.ready', 1, { state: 'ready' })
    emitControl(room, 'turn.listening', 2)
    emitControl(room, 'turn.media-ready', 3, mediaReadyPayload())
    expect(AudioPlaybackBoundary.prototype.setTrack).toHaveBeenLastCalledWith(track, 1)

    const beforeInvalid = vi.mocked(AudioPlaybackBoundary.prototype.suspend).mock.calls.length
    emitControl(room, 'turn.interrupted', 4, {}, true, 1, 1, 99)
    expect(AudioPlaybackBoundary.prototype.suspend).toHaveBeenCalledTimes(beforeInvalid)
    expect(observed.onDrop).toHaveBeenCalledTimes(1)

    emitControl(room, 'turn.interrupted', 5, {}, true)
    expect(AudioPlaybackBoundary.prototype.suspend).toHaveBeenCalledTimes(beforeInvalid + 1)
    emitControl(room, 'turn.listening', 6, {}, false, 1, 2, 2, 'turn-00000002')
    emitControl(room, 'turn.media-ready', 7, mediaReadyPayload(), false, 1, 2, 2, 'turn-00000002')
    expect(AudioPlaybackBoundary.prototype.setTrack).toHaveBeenLastCalledWith(track, 2)

    const beforeOld = vi.mocked(AudioPlaybackBoundary.prototype.suspend).mock.calls.length
    emitControl(room, 'turn.interrupted', 8, {}, true, 1, 1, 1)
    expect(AudioPlaybackBoundary.prototype.suspend).toHaveBeenCalledTimes(beforeOld)
    emitControl(room, 'turn.interrupted', 9, {}, true, 1, 2, 2, 'turn-00000002')
    expect(AudioPlaybackBoundary.prototype.suspend).toHaveBeenCalledTimes(beforeOld + 1)
  })

  it('retries one logical reset when delivery resolves without an observed ACK', async () => {
    vi.useFakeTimers()
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()
    const room = livekit.rooms[0]
    emitControl(room, 'session.ready', 1, { state: 'ready' })
    const initialTrack = { kind: 'audio' }
    const recoveredTrack = { kind: 'audio' }
    room.emit(
      'trackSubscribed', initialTrack, { trackSid: 'publication-test' }, { identity: 'agent-session-test-0001' },
    )
    emitControl(room, 'turn.listening', 2)
    emitControl(room, 'turn.media-ready', 3, mediaReadyPayload())

    room.emit('reconnecting')
    room.emit('reconnected')
    room.emit(
      'trackSubscribed', recoveredTrack, { trackSid: 'publication-test' }, { identity: 'agent-session-test-0001' },
    )
    await vi.advanceTimersByTimeAsync(600)

    expect(AudioPlaybackBoundary.prototype.suspend).toHaveBeenCalledTimes(2)
    expect(AudioPlaybackBoundary.prototype.setTrack).toHaveBeenCalledTimes(1)
    expect(AudioPlaybackBoundary.prototype.reset).not.toHaveBeenCalled()

    expect(room.localParticipant.publishData).toHaveBeenCalledTimes(2)
    const [firstBytes, options] = room.localParticipant.publishData.mock.calls[0]
    const [retryBytes] = room.localParticipant.publishData.mock.calls[1]
    const firstRequest = JSON.parse(new TextDecoder().decode(firstBytes))
    expect(firstRequest).toMatchObject({
      type: 'client.reconnected',
      stream_epoch: 1,
    })
    expect(new TextDecoder().decode(retryBytes)).toBe(new TextDecoder().decode(firstBytes))
    expect(options.topic).toBe('voice-agent.client-control.v1')

    emitControl(room, 'session.reconnected', 10, { state: 'ready' }, false, 2)
    expect(AudioPlaybackBoundary.prototype.setTrack).toHaveBeenCalledTimes(1)
    emitControl(room, 'session.ready', 11, { state: 'ready' }, false, 2)
    await vi.advanceTimersByTimeAsync(1_000)

    expect(AudioPlaybackBoundary.prototype.setTrack).toHaveBeenCalledTimes(1)
    expect(AudioPlaybackBoundary.prototype.setTrack).toHaveBeenLastCalledWith(initialTrack, 1)
    expect(room.localParticipant.publishData).toHaveBeenCalledTimes(2)
    expect(observed.onConnection).toHaveBeenCalledWith('reconnecting')
    expect(observed.onControl).toHaveBeenCalledWith(expect.objectContaining({
      type: 'session.ready', stream_epoch: 2,
    }))
  })

  it('recovers when the first reconnect ACK is lost and drops outage controls', async () => {
    vi.useFakeTimers()
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()
    const room = livekit.rooms[0]
    emitControl(room, 'session.ready', 1, { state: 'ready' })
    emitControl(room, 'turn.listening', 2)

    room.emit('reconnecting')
    room.emit('reconnected')
    emitControl(room, 'llm.visible', 3, { response: 'stale old epoch' })
    emitControl(room, 'session.ready', 4, { state: 'ready' }, false, 2)
    await vi.advanceTimersByTimeAsync(600)

    expect(room.localParticipant.publishData).toHaveBeenCalledTimes(2)
    expect(observed.onDrop).toHaveBeenCalledTimes(2)
    emitControl(room, 'session.reconnected', 5, { state: 'ready' }, false, 2)
    emitControl(room, 'session.ready', 6, { state: 'ready' }, false, 2)
    emitControl(room, 'turn.listening', 7, {}, false, 2)

    expect(observed.onControl).not.toHaveBeenCalledWith(expect.objectContaining({
      payload: expect.objectContaining({ response: 'stale old epoch' }),
    }))
    expect(observed.onControl).toHaveBeenLastCalledWith(expect.objectContaining({
      type: 'turn.listening', stream_epoch: 2,
    }))
  })

  it('fails reconnect in bounded time when no reset ACK arrives', async () => {
    vi.useFakeTimers()
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()
    const room = livekit.rooms[0]
    emitControl(room, 'session.ready', 1, { state: 'ready' })

    room.emit('reconnecting')
    room.emit('reconnected')
    await vi.advanceTimersByTimeAsync(5_100)

    expect(room.localParticipant.publishData.mock.calls.length).toBeGreaterThan(1)
    expect(room.localParticipant.publishData.mock.calls.length).toBeLessThanOrEqual(11)
    expect(observed.onConnection).toHaveBeenCalledWith(
      'failed', 'Сервер не подтвердил восстановление сессии',
    )
  })

  it('does not reset playback or session for signal-only reconnects', async () => {
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()
    const room = livekit.rooms[0]
    room.emit(
      'trackSubscribed', { kind: 'audio' }, {}, { identity: 'agent-session-test-0001' },
    )

    room.emit('signalReconnecting')
    room.emit('reconnected')
    await Promise.resolve()

    expect(room.localParticipant.publishData).not.toHaveBeenCalled()
    expect(AudioPlaybackBoundary.prototype.reset).not.toHaveBeenCalled()
    expect(observed.onConnection).not.toHaveBeenCalledWith('reconnecting')
  })

  it('mutes and unmutes the existing published microphone without control messages', async () => {
    const track = {
      isMuted: false,
      mute: vi.fn(async function (this: { isMuted: boolean }) { this.isMuted = true }),
      unmute: vi.fn(async function (this: { isMuted: boolean }) { this.isMuted = false }),
      stop: vi.fn(),
    }
    livekit.createLocalAudioTrack.mockResolvedValueOnce(track)
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()
    const room = livekit.rooms[0]

    expect(observed.onMicrophoneLifecycle).toHaveBeenNthCalledWith(1, 'requesting-permission')
    expect(observed.onMicrophoneLifecycle).toHaveBeenNthCalledWith(2, 'publishing')
    expect(observed.onMicrophoneLifecycle).toHaveBeenNthCalledWith(3, 'live')
    expect(observed.onMicrophoneState).toHaveBeenLastCalledWith(true, false)
    await client.setMicrophoneEnabled(false)
    expect(track.mute).toHaveBeenCalledTimes(1)
    expect(observed.onMicrophoneLifecycle).toHaveBeenLastCalledWith('muted')
    expect(observed.onMicrophoneState).toHaveBeenLastCalledWith(false, false, undefined)

    await client.setMicrophoneEnabled(true)
    expect(track.unmute).toHaveBeenCalledTimes(1)
    expect(observed.onMicrophoneLifecycle).toHaveBeenLastCalledWith('live')
    expect(observed.onMicrophoneState).toHaveBeenLastCalledWith(true, false, undefined)
    expect(room.localParticipant.publishTrack).toHaveBeenCalledTimes(1)
    expect(room.localParticipant.publishData).not.toHaveBeenCalled()
  })

  it('reports a permission failure before publication without inventing an active microphone', async () => {
    livekit.createLocalAudioTrack.mockRejectedValueOnce(new DOMException('denied', 'NotAllowedError'))
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)

    await expect(client.start()).rejects.toThrow('denied')

    expect(observed.onMicrophoneLifecycle).toHaveBeenNthCalledWith(1, 'requesting-permission')
    expect(observed.onMicrophoneLifecycle).toHaveBeenLastCalledWith('error')
    expect(livekit.rooms[0].localParticipant.publishTrack).not.toHaveBeenCalled()
    await client.stop()
    expect(observed.onMicrophoneLifecycle).toHaveBeenLastCalledWith('error')
    expect(observed.onMicrophoneState).toHaveBeenLastCalledWith(false, false)
  })

  it('publishes disconnected microphone truth when a non-device session failure releases capture', async () => {
    const track = {
      isMuted: false,
      mute: vi.fn().mockResolvedValue(undefined),
      unmute: vi.fn().mockResolvedValue(undefined),
      stop: vi.fn(),
    }
    livekit.createLocalAudioTrack.mockResolvedValueOnce(track)
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()

    livekit.rooms[0].emit('disconnected')
    await vi.waitFor(() => expect(observed.onConnection).toHaveBeenCalledWith(
      'failed', 'Соединение с голосовой сессией потеряно',
    ))

    expect(track.stop).toHaveBeenCalledTimes(1)
    expect(observed.onMicrophoneState).toHaveBeenLastCalledWith(false, false)
    expect(observed.onMicrophoneLifecycle).toHaveBeenLastCalledWith('disconnected')
  })

  it('preserves microphone error while device-failure cleanup publishes disabled truth', async () => {
    const track = {
      isMuted: false,
      mute: vi.fn().mockResolvedValue(undefined),
      unmute: vi.fn().mockResolvedValue(undefined),
      stop: vi.fn(),
    }
    livekit.createLocalAudioTrack.mockResolvedValueOnce(track)
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()

    livekit.rooms[0].emit('mediaDevicesError')
    await vi.waitFor(() => expect(observed.onConnection).toHaveBeenCalledWith(
      'failed', 'Микрофон недоступен',
    ))

    expect(track.stop).toHaveBeenCalledTimes(1)
    expect(observed.onMicrophoneState).toHaveBeenLastCalledWith(false, false)
    expect(observed.onMicrophoneLifecycle).toHaveBeenLastCalledWith('error')
  })

  it('coalesces rapid microphone toggles to the final requested state', async () => {
    let releaseMute: (() => void) | undefined
    const muteBlocked = new Promise<void>((resolve) => { releaseMute = resolve })
    const track = {
      isMuted: false,
      mute: vi.fn(async function (this: { isMuted: boolean }) {
        await muteBlocked
        this.isMuted = true
      }),
      unmute: vi.fn(async function (this: { isMuted: boolean }) { this.isMuted = false }),
      stop: vi.fn(),
    }
    livekit.createLocalAudioTrack.mockResolvedValueOnce(track)
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()

    const off = client.toggleMicrophone()
    await vi.waitFor(() => expect(track.mute).toHaveBeenCalledTimes(1))
    const on = client.toggleMicrophone()
    releaseMute?.()
    await Promise.all([off, on])

    expect(track.unmute).toHaveBeenCalledTimes(1)
    expect(observed.onMicrophoneState).toHaveBeenLastCalledWith(true, false, undefined)
    expect(livekit.rooms[0].localParticipant.publishTrack).toHaveBeenCalledTimes(1)
  })

  it('preserves muted capture across a transient reconnect', async () => {
    const track = {
      isMuted: false,
      mute: vi.fn(async function (this: { isMuted: boolean }) { this.isMuted = true }),
      unmute: vi.fn(async function (this: { isMuted: boolean }) { this.isMuted = false }),
      stop: vi.fn(),
    }
    livekit.createLocalAudioTrack.mockResolvedValueOnce(track)
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()
    const room = livekit.rooms[0]
    emitControl(room, 'session.ready', 1, { state: 'ready' })
    await client.setMicrophoneEnabled(false)

    room.emit('reconnecting')
    room.emit('reconnected')
    emitControl(room, 'session.reconnected', 2, { state: 'ready' }, false, 2)
    emitControl(room, 'session.ready', 3, { state: 'ready' }, false, 2)

    expect(track.isMuted).toBe(true)
    expect(track.unmute).not.toHaveBeenCalled()
    expect(room.localParticipant.publishTrack).toHaveBeenCalledTimes(1)
    expect(observed.onMicrophoneState).toHaveBeenLastCalledWith(false, false, undefined)
  })

  it('retains effective microphone truth and reports a bounded transition failure', async () => {
    const track = {
      isMuted: false,
      mute: vi.fn().mockRejectedValue(new Error('device transition failed')),
      unmute: vi.fn().mockResolvedValue(undefined),
      stop: vi.fn(),
    }
    livekit.createLocalAudioTrack.mockResolvedValueOnce(track)
    const observed = callbacks()
    const client = new VoiceClient(document.createElement('div'), observed)
    await client.start()

    await client.setMicrophoneEnabled(false)

    expect(observed.onMicrophoneLifecycle).toHaveBeenLastCalledWith('error')
    expect(observed.onMicrophoneState).toHaveBeenLastCalledWith(
      true,
      false,
      'Не удалось выключить микрофон. Повторите попытку.',
    )
    expect(livekit.rooms[0].localParticipant.publishData).not.toHaveBeenCalled()
    expect(AudioPlaybackBoundary.prototype.suspend).not.toHaveBeenCalled()
  })
})
