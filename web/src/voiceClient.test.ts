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

    constructor() {
      rooms.push(this)
    }

    on(): this {
      return this
    }
  }

  return { FakeRoom, createLocalAudioTrack, rooms }
})

vi.mock('livekit-client', () => ({
  Room: livekit.FakeRoom,
  RoomEvent: {
    TrackSubscribed: 'trackSubscribed',
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

afterEach(() => {
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
})
