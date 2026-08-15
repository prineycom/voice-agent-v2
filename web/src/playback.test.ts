import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  AudioPlaybackBoundary,
  speechEnvelopeFromTimeDomain,
  type AttachableAudioTrack,
} from './playback'

function track(play: () => Promise<void>): AttachableAudioTrack {
  return {
    attach: vi.fn((element: HTMLMediaElement) => {
      element.play = play
      element.pause = vi.fn()
      element.load = vi.fn()
      return element
    }),
    detach: vi.fn((element) => element === undefined ? [] : [element]),
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
    let element!: HTMLMediaElement
    let paused = true
    let ended = false
    const observations: number[] = []
    const boundary = new AudioPlaybackBoundary(
      document.createElement('div'), vi.fn(), ({ level }) => observations.push(level),
    )
    boundary.setTrack({
      attach: (requestedElement) => {
        element = requestedElement
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
        return element
      },
      detach: (detachedElement) => detachedElement === undefined ? [] : [detachedElement],
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
    const remote: AttachableAudioTrack = {
      attach: vi.fn((requestedElement: HTMLMediaElement) => prepareElement(requestedElement)),
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
    const initialElement = vi.mocked(remote.attach).mock.calls[0]?.[0]
    const replacement = vi.mocked(remote.attach).mock.calls[1]?.[0]
    expect(initialElement).toBeInstanceOf(HTMLAudioElement)
    expect(replacement).toBeInstanceOf(HTMLAudioElement)
    expect(replacement).not.toBe(initialElement)
    expect(remote.detach).toHaveBeenCalledWith(initialElement)
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

    let element!: HTMLMediaElement
    const remote: AttachableAudioTrack = {
      attach: vi.fn((requestedElement: HTMLMediaElement) => {
        element = requestedElement
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
      }),
      detach: vi.fn((detachedElement) => detachedElement === undefined ? [] : [detachedElement]),
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

  it('keeps decoded envelope routing available across attachment generations', async () => {
    const routedElements = new Set<HTMLMediaElement>()
    const createMediaElementSource = vi.fn((element: HTMLMediaElement) => {
      if (routedElements.has(element)) throw new Error('element was already routed')
      routedElements.add(element)
      return { connect: vi.fn(), disconnect: vi.fn() }
    })
    class FakeAudioContext {
      state = 'running' as AudioContextState
      destination = {}
      onstatechange: ((this: BaseAudioContext, ev: Event) => unknown) | null = null
      createAnalyser = vi.fn(() => ({
        fftSize: 256,
        smoothingTimeConstant: 0,
        connect: vi.fn(),
        disconnect: vi.fn(),
        getByteTimeDomainData: vi.fn(),
      }))
      createMediaElementSource = createMediaElementSource
      resume = vi.fn().mockResolvedValue(undefined)
      close = vi.fn().mockResolvedValue(undefined)
    }
    vi.stubGlobal('AudioContext', FakeAudioContext)
    vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1))
    vi.stubGlobal('cancelAnimationFrame', vi.fn())

    const remote: AttachableAudioTrack = {
      attach: vi.fn((element: HTMLMediaElement) => {
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
      }),
      detach: vi.fn((element) => element === undefined ? [] : [element]),
    }
    const envelopeStatus = vi.fn()
    const boundary = new AudioPlaybackBoundary(
      document.createElement('div'), vi.fn(), () => undefined, envelopeStatus,
    )

    boundary.setTrack(remote)
    await Promise.resolve()
    await Promise.resolve()
    boundary.suspend()
    boundary.reset()
    await Promise.resolve()
    await Promise.resolve()

    expect(createMediaElementSource).toHaveBeenCalledTimes(2)
    const firstElement = createMediaElementSource.mock.calls[0]?.[0]
    const secondElement = createMediaElementSource.mock.calls[1]?.[0]
    expect(secondElement).not.toBe(firstElement)
    expect(envelopeStatus).toHaveBeenLastCalledWith('available')
  })

  it('restores fresh direct playout when a routed context stops running', async () => {
    const contexts: FakeAudioContext[] = []
    class FakeAudioContext {
      state = 'running' as AudioContextState
      destination = {}
      onstatechange: ((this: BaseAudioContext, ev: Event) => unknown) | null = null
      createAnalyser = vi.fn(() => ({
        fftSize: 256,
        smoothingTimeConstant: 0,
        connect: vi.fn(),
        disconnect: vi.fn(),
        getByteTimeDomainData: vi.fn(),
      }))
      createMediaElementSource = vi.fn(() => ({ connect: vi.fn(), disconnect: vi.fn() }))
      resume = vi.fn().mockRejectedValue(new Error('context remains suspended'))
      close = vi.fn().mockResolvedValue(undefined)

      constructor() {
        contexts.push(this)
      }
    }
    vi.stubGlobal('AudioContext', FakeAudioContext)
    vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1))
    vi.stubGlobal('cancelAnimationFrame', vi.fn())

    const remote: AttachableAudioTrack = {
      attach: vi.fn((element: HTMLMediaElement) => {
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
      }),
      detach: vi.fn((element) => element === undefined ? [] : [element]),
    }
    const blocked = vi.fn()
    const envelopeStatus = vi.fn()
    const container = document.createElement('div')
    const boundary = new AudioPlaybackBoundary(
      container, blocked, () => undefined, envelopeStatus,
    )

    boundary.setTrack(remote)
    await Promise.resolve()
    await Promise.resolve()
    const routedElement = vi.mocked(remote.attach).mock.calls[0]?.[0]
    const context = contexts[0]
    expect(context).toBeDefined()
    context!.state = 'suspended'
    context!.onstatechange?.call(context as unknown as BaseAudioContext, new Event('statechange'))
    await vi.waitFor(() => {
      expect(remote.attach).toHaveBeenCalledTimes(2)
    })

    expect(context!.resume).toHaveBeenCalledTimes(1)
    const directElement = vi.mocked(remote.attach).mock.calls[1]?.[0]
    expect(directElement).not.toBe(routedElement)
    expect(container.firstElementChild).toBe(directElement)
    expect(blocked).toHaveBeenCalledWith(true)
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

  it('reattaches the same track on a distinct explicit element after suspension', () => {
    const boundary = new AudioPlaybackBoundary(document.createElement('div'), vi.fn())
    const remote = track(vi.fn().mockResolvedValue(undefined))

    boundary.setTrack(remote)
    boundary.suspend()
    boundary.reset()

    expect(remote.attach).toHaveBeenCalledTimes(2)
    const firstElement = vi.mocked(remote.attach).mock.calls[0]?.[0]
    const secondElement = vi.mocked(remote.attach).mock.calls[1]?.[0]
    expect(firstElement).toBeInstanceOf(HTMLAudioElement)
    expect(secondElement).toBeInstanceOf(HTMLAudioElement)
    expect(secondElement).not.toBe(firstElement)
  })
})
