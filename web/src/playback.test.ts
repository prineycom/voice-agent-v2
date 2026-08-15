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

afterEach(() => vi.unstubAllGlobals())

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
