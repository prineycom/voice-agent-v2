import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  AudioPlaybackBoundary,
  speechEnvelopeFromTimeDomain,
  type AttachableAudioTrack,
} from './playback'

function track(play: () => Promise<void>): AttachableAudioTrack {
  const element = document.createElement('audio')
  element.play = play
  element.pause = vi.fn()
  element.load = vi.fn()
  return {
    attach: vi.fn(() => element),
    detach: vi.fn(() => [element]),
  }
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('persistent playback observation', () => {
  it('derives a bounded deterministic envelope from decoded time-domain samples', () => {
    expect(speechEnvelopeFromTimeDomain(new Uint8Array([128, 128, 128]))).toBe(0)
    const speech = speechEnvelopeFromTimeDomain(new Uint8Array([64, 192, 64, 192]))
    expect(speech).toBeGreaterThan(0)
    expect(speech).toBeLessThanOrEqual(1)
    expect(speechEnvelopeFromTimeDomain(new Uint8Array([64, 192, 64, 192]))).toBe(speech)
  })

  it('observes only active decoded media-element output and clears stalled or paused playout', async () => {
    let animationFrame: FrameRequestCallback | null = null
    const analyser = {
      fftSize: 0,
      smoothingTimeConstant: 0,
      connect: vi.fn(),
      disconnect: vi.fn(),
      getByteTimeDomainData: vi.fn((samples: Uint8Array) => {
        for (let index = 0; index < samples.length; index += 1) samples[index] = index % 2 ? 192 : 64
      }),
    }
    const source = { connect: vi.fn(), disconnect: vi.fn() }
    const context = {
      destination: {},
      createAnalyser: vi.fn(() => analyser),
      createMediaElementSource: vi.fn(() => source),
      resume: vi.fn().mockResolvedValue(undefined),
      close: vi.fn().mockResolvedValue(undefined),
    }
    class FakeAudioContext {
      state = 'running' as AudioContextState
      destination = context.destination
      createAnalyser = context.createAnalyser
      createMediaElementSource = context.createMediaElementSource
      resume = context.resume
      close = context.close
    }
    vi.stubGlobal('AudioContext', FakeAudioContext)
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => {
      animationFrame = callback
      return 1
    }))
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
    const element = document.createElement('audio')
    let paused = true
    let ended = false
    Object.defineProperties(element, {
      paused: { configurable: true, get: () => paused },
      ended: { configurable: true, get: () => ended },
      readyState: { configurable: true, get: () => 4 },
      error: { configurable: true, get: () => null },
    })
    element.play = vi.fn(() => {
      paused = false
      element.dispatchEvent(new Event('playing'))
      return Promise.resolve()
    })
    element.pause = vi.fn(() => {
      paused = true
      element.dispatchEvent(new Event('pause'))
    })
    element.load = vi.fn()
    const observations: number[] = []
    const boundary = new AudioPlaybackBoundary(
      document.createElement('div'), vi.fn(), ({ level }) => observations.push(level),
    )
    boundary.setTrack({
      attach: () => element,
      detach: () => [element],
      mediaStreamTrack: {} as MediaStreamTrack,
    })
    await Promise.resolve()
    await Promise.resolve()
    expect(context.createMediaElementSource).toHaveBeenCalledWith(element)
    expect(source.connect).toHaveBeenCalledWith(analyser)
    expect(analyser.connect).toHaveBeenCalledWith(context.destination)
    expect(animationFrame).not.toBeNull()
    ;(animationFrame as unknown as FrameRequestCallback)(40)
    expect(observations.at(-1)).toBeGreaterThan(0)

    element.dispatchEvent(new Event('stalled'))
    expect(observations.at(-1)).toBe(0)
    const stalledObservationCount = observations.length
    ;(animationFrame as unknown as FrameRequestCallback)(80)
    expect(observations).toHaveLength(stalledObservationCount)

    element.dispatchEvent(new Event('playing'))
    ;(animationFrame as unknown as FrameRequestCallback)(120)
    expect(observations.at(-1)).toBeGreaterThan(0)
    element.pause()
    expect(observations.at(-1)).toBe(0)

    paused = false
    ended = true
    element.dispatchEvent(new Event('playing'))
    ;(animationFrame as unknown as FrameRequestCallback)(160)
    expect(observations.at(-1)).toBe(0)

    boundary.suspend()
    expect(observations.at(-1)).toBe(0)
    expect(context.close).toHaveBeenCalledTimes(1)
  })

  it('restores direct playout on a fresh element when analyser routing fails', async () => {
    const source = {
      connect: vi.fn(() => {
        throw new Error('routing failed')
      }),
      disconnect: vi.fn(),
    }
    const analyser = {
      fftSize: 0,
      smoothingTimeConstant: 0,
      connect: vi.fn(),
      disconnect: vi.fn(),
    }
    const context = {
      destination: {},
      createAnalyser: vi.fn(() => analyser),
      createMediaElementSource: vi.fn(() => source),
      resume: vi.fn().mockResolvedValue(undefined),
      close: vi.fn().mockResolvedValue(undefined),
    }
    class FakeAudioContext {
      state = 'running' as AudioContextState
      destination = context.destination
      createAnalyser = context.createAnalyser
      createMediaElementSource = context.createMediaElementSource
      resume = context.resume
      close = context.close
    }
    vi.stubGlobal('AudioContext', FakeAudioContext)

    const pooledElement = document.createElement('audio')
    const prepareElement = (element: HTMLMediaElement): HTMLMediaElement => {
      Object.defineProperties(element, {
        paused: { configurable: true, get: () => false },
        ended: { configurable: true, get: () => false },
        readyState: { configurable: true, get: () => 4 },
        error: { configurable: true, get: () => null },
      })
      element.play = vi.fn().mockResolvedValue(undefined)
      element.pause = vi.fn()
      element.load = vi.fn()
      return element
    }
    prepareElement(pooledElement)
    const remote: AttachableAudioTrack = {
      attach: vi.fn((requestedElement?: HTMLMediaElement) => (
        requestedElement === undefined
          ? pooledElement
          : prepareElement(requestedElement)
      )),
      detach: vi.fn((element) => element === undefined ? [] : [element]),
    }
    const container = document.createElement('div')
    const blocked = vi.fn()
    const envelopeStatus = vi.fn()
    const boundary = new AudioPlaybackBoundary(
      container,
      blocked,
      () => undefined,
      envelopeStatus,
    )

    boundary.setTrack(remote)
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()

    expect(remote.attach).toHaveBeenCalledTimes(2)
    expect(remote.attach).toHaveBeenNthCalledWith(1, undefined)
    const replacement = vi.mocked(remote.attach).mock.calls[1]?.[0]
    expect(replacement).toBeInstanceOf(HTMLAudioElement)
    expect(replacement).not.toBe(pooledElement)
    expect(remote.detach).toHaveBeenCalledWith(pooledElement)
    expect(container.firstElementChild).toBe(replacement)
    expect(replacement?.play).toHaveBeenCalledTimes(1)
    expect(source.disconnect).toHaveBeenCalledTimes(1)
    expect(context.close).toHaveBeenCalledTimes(1)
    expect(blocked).toHaveBeenLastCalledWith(false)
    expect(envelopeStatus).toHaveBeenLastCalledWith('unavailable')
  })

  it('preserves direct playout when a suspended audio context cannot start promptly', async () => {
    vi.useFakeTimers()
    const createMediaElementSource = vi.fn()
    const close = vi.fn().mockResolvedValue(undefined)
    class SuspendedAudioContext {
      state = 'suspended' as AudioContextState
      destination = {}
      createAnalyser = vi.fn()
      createMediaElementSource = createMediaElementSource
      resume = vi.fn(() => new Promise<void>(() => undefined))
      close = close
    }
    vi.stubGlobal('AudioContext', SuspendedAudioContext)

    const element = document.createElement('audio')
    Object.defineProperties(element, {
      paused: { configurable: true, get: () => false },
      ended: { configurable: true, get: () => false },
      readyState: { configurable: true, get: () => 4 },
      error: { configurable: true, get: () => null },
    })
    element.play = vi.fn().mockResolvedValue(undefined)
    element.pause = vi.fn()
    element.load = vi.fn()
    const remote: AttachableAudioTrack = {
      attach: vi.fn(() => element),
      detach: vi.fn(() => [element]),
    }
    const container = document.createElement('div')
    const blocked = vi.fn()
    const envelopeStatus = vi.fn()
    const boundary = new AudioPlaybackBoundary(
      container,
      blocked,
      () => undefined,
      envelopeStatus,
    )

    boundary.setTrack(remote)
    await Promise.resolve()
    await Promise.resolve()
    expect(createMediaElementSource).not.toHaveBeenCalled()
    expect(container.firstElementChild).toBe(element)

    await vi.advanceTimersByTimeAsync(251)

    expect(createMediaElementSource).not.toHaveBeenCalled()
    expect(remote.attach).toHaveBeenCalledTimes(1)
    expect(remote.detach).not.toHaveBeenCalled()
    expect(element.play).toHaveBeenCalledTimes(1)
    expect(close).toHaveBeenCalledTimes(1)
    expect(blocked).toHaveBeenLastCalledWith(false)
    expect(envelopeStatus).toHaveBeenLastCalledWith('unavailable')
  })

  it('attaches one persistent track and reports autoplay failure without throwing', async () => {
    const blocked = vi.fn()
    const boundary = new AudioPlaybackBoundary(document.createElement('div'), blocked)
    const remote = track(vi.fn().mockRejectedValue(new Error('autoplay blocked')))

    expect(() => boundary.setTrack(remote)).not.toThrow()
    await Promise.resolve()

    expect(remote.attach).toHaveBeenCalledTimes(1)
    expect(blocked).toHaveBeenLastCalledWith(true)
  })

  it('ignores old publication generations and suspends only the matching one', () => {
    const boundary = new AudioPlaybackBoundary(document.createElement('div'), vi.fn())
    const current = track(vi.fn().mockResolvedValue(undefined))
    const stale = track(vi.fn().mockResolvedValue(undefined))

    boundary.setTrack(current, 2)
    boundary.setTrack(stale, 1)
    expect(stale.attach).not.toHaveBeenCalled()
    expect(boundary.suspend(1)).toBe(false)
    expect(current.detach).not.toHaveBeenCalled()
    expect(boundary.suspend(2)).toBe(true)
    expect(current.detach).toHaveBeenCalledTimes(1)
  })

  it('reattaches the same track after a brief suspension', () => {
    const boundary = new AudioPlaybackBoundary(document.createElement('div'), vi.fn())
    const remote = track(vi.fn().mockResolvedValue(undefined))

    boundary.setTrack(remote)
    boundary.suspend()
    boundary.reset()

    expect(remote.attach).toHaveBeenCalledTimes(2)
  })
})
