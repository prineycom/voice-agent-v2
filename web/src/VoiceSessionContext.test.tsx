import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const livekit = vi.hoisted(() => {
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
    on = vi.fn().mockReturnThis()
  }

  return { createLocalAudioTrack, FakeRoom }
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

import { VoiceSessionProvider, useVoiceSession } from './VoiceSessionContext'
import { ACTIVE_LLM_MODEL_IDENTITY } from './state'

function capabilityResponse(): Response {
  return {
    ok: true,
    json: vi.fn().mockResolvedValue({
      session_id: 'session-test-0001',
      stream_epoch: 1,
      livekit_url: 'wss://voice.example.invalid:7443',
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

function SessionProbe() {
  const { state, connect, disconnect, audioContainerRef } = useVoiceSession()
  return (
    <>
      <button type="button" onClick={() => void connect()}>CONNECT</button>
      <button type="button" onClick={() => void disconnect()}>DISCONNECT</button>
      <output aria-label="connection state">{state.connection}</output>
      <output aria-label="microphone state">{state.microphoneStatus}</output>
      <div ref={audioContainerRef} />
    </>
  )
}

beforeEach(() => {
  sessionStorage.clear()
  livekit.createLocalAudioTrack.mockRejectedValue(new DOMException('denied', 'NotAllowedError'))
  let postCount = 0
  vi.stubGlobal('fetch', vi.fn((_url: string, options?: RequestInit) => {
    if (options?.method === 'DELETE') {
      return Promise.resolve({ ok: true, status: 204 } as Response)
    }
    postCount += 1
    return Promise.resolve(
      postCount === 1 ? capabilityResponse() : ({ ok: false, status: 503 } as Response),
    )
  }))
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  livekit.createLocalAudioTrack.mockReset()
})

describe('VoiceSessionProvider connection attempts', () => {
  it('retains a reload admission through provider unmount cleanup, then explicitly releases it', async () => {
    const user = userEvent.setup()
    const firstMicrophone = {
      isMuted: false,
      mute: vi.fn().mockResolvedValue(undefined),
      unmute: vi.fn().mockResolvedValue(undefined),
      stop: vi.fn(),
    }
    const reloadedMicrophone = {
      isMuted: false,
      mute: vi.fn().mockResolvedValue(undefined),
      unmute: vi.fn().mockResolvedValue(undefined),
      stop: vi.fn(),
    }
    let releaseMicrophone: (track: typeof firstMicrophone) => void = () => undefined
    const pendingMicrophone = new Promise<typeof firstMicrophone>((resolve) => {
      releaseMicrophone = resolve
    })
    livekit.createLocalAudioTrack
      .mockImplementationOnce(() => pendingMicrophone)
      .mockResolvedValueOnce(reloadedMicrophone)
    vi.stubGlobal('fetch', vi.fn((_url: string, options?: RequestInit) => Promise.resolve(
      options?.method === 'DELETE'
        ? ({ ok: true, status: 204 } as Response)
        : capabilityResponse(),
    )))

    const initialDocument = render(
      <VoiceSessionProvider>
        <SessionProbe />
      </VoiceSessionProvider>,
    )
    await user.click(screen.getByRole('button', { name: 'CONNECT' }))
    await waitFor(() => expect(livekit.createLocalAudioTrack).toHaveBeenCalledTimes(1))
    const initialPost = vi.mocked(fetch).mock.calls.find(([, options]) => options?.method === 'POST')
    const attemptIdentity = (initialPost?.[1]?.headers as Record<string, string>)
      ['X-Voice-Session-Attempt']

    // This follows the old document's unmount path while start() is still
    // pending, then mounts the replacement document with the same tab storage.
    initialDocument.unmount()
    releaseMicrophone(firstMicrophone)
    await waitFor(() => expect(firstMicrophone.stop).toHaveBeenCalledTimes(1))
    expect(vi.mocked(fetch).mock.calls.filter(([, options]) => options?.method === 'DELETE'))
      .toHaveLength(0)
    expect(sessionStorage.getItem('voice-agent.session-attempt.v1')).toBe(attemptIdentity)

    const reloadedDocument = render(
      <VoiceSessionProvider>
        <SessionProbe />
      </VoiceSessionProvider>,
    )
    await user.click(screen.getByRole('button', { name: 'CONNECT' }))
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.filter(
      ([, options]) => options?.method === 'POST',
    )).toHaveLength(2))
    const reloadPost = vi.mocked(fetch).mock.calls.filter(
      ([, options]) => options?.method === 'POST',
    )[1]
    expect((reloadPost[1]?.headers as Record<string, string>)['X-Voice-Session-Attempt'])
      .toBe(attemptIdentity)

    await user.click(screen.getByRole('button', { name: 'DISCONNECT' }))
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.filter(
      ([, options]) => options?.method === 'DELETE',
    )).toHaveLength(1))
    expect(sessionStorage.getItem('voice-agent.session-attempt.v1')).toBeNull()
    reloadedDocument.unmount()
  })

  it('does not carry a microphone failure into an earlier-stage retry failure', async () => {
    const user = userEvent.setup()
    render(
      <VoiceSessionProvider>
        <SessionProbe />
      </VoiceSessionProvider>,
    )

    await user.click(screen.getByRole('button', { name: 'CONNECT' }))
    await waitFor(() => {
      expect(screen.getByLabelText('connection state').textContent).toBe('failed')
      expect(screen.getByLabelText('microphone state').textContent).toBe('error')
    })

    await user.click(screen.getByRole('button', { name: 'CONNECT' }))
    await waitFor(() => {
      expect(screen.getByLabelText('connection state').textContent).toBe('failed')
      expect(screen.getByLabelText('microphone state').textContent).toBe('disconnected')
    })

    const calls = vi.mocked(fetch).mock.calls
    expect(calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(2)
    expect(calls.filter(([, options]) => options?.method === 'DELETE')).toHaveLength(1)
    expect(livekit.createLocalAudioTrack).toHaveBeenCalledTimes(1)
  })
})
