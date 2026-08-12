import { afterEach, describe, expect, it, vi } from 'vitest'
import { AudioPlaybackBoundary, type AttachableAudioTrack } from './playback'

class FiniteMediaTrack extends EventTarget {
  readonly id = 'finite-track'
  readyState: MediaStreamTrackState = 'live'
  muted = false
}

class FakeTrack implements AttachableAudioTrack {
  attachCount = 0
  detachCount = 0
  readonly finiteTrack = new FiniteMediaTrack()
  readonly mediaStreamTrack = this.finiteTrack as unknown as MediaStreamTrack
  readonly elements: HTMLMediaElement[] = []
  receivedSamples = 0
  emittedSamples = 0
  concealedSamples = 0
  setAudioContext = vi.fn()
  setWebAudioPlugins = vi.fn()

  async getRTCStatsReport(): Promise<RTCStatsReport> {
    return new Map([[
      'audio',
      {
        type: 'inbound-rtp',
        kind: 'audio',
        trackIdentifier: this.finiteTrack.id,
        totalSamplesReceived: this.receivedSamples,
        jitterBufferEmittedCount: this.emittedSamples,
        concealedSamples: this.concealedSamples,
      },
    ]]) as unknown as RTCStatsReport
  }

  attach(): HTMLMediaElement {
    this.attachCount += 1
    const element = document.createElement('audio')
    element.play = vi.fn().mockResolvedValue(undefined)
    element.pause = vi.fn()
    element.load = vi.fn()
    this.elements.push(element)
    return element
  }

  detach(): HTMLMediaElement[] {
    this.detachCount += 1
    return []
  }

  end(): void {
    this.finiteTrack.readyState = 'ended'
    this.finiteTrack.dispatchEvent(new Event('ended'))
  }
}

class RenderHarness {
  state: AudioContextState = 'running'
  readonly sampleRate = 48_000
  readonly resume = vi.fn(async () => { this.state = 'running' })
  readonly close = vi.fn(async () => { this.state = 'closed' })
  readonly node = {
    onaudioprocess: null as ((event: AudioProcessingEvent) => void) | null,
    disconnect: vi.fn(),
  } as unknown as ScriptProcessorNode

  createScriptProcessor = vi.fn().mockReturnValue(this.node)

  render(frames: number): void {
    const input = new Float32Array(frames)
    const output = new Float32Array(frames)
    this.node.onaudioprocess?.({
      inputBuffer: {
        numberOfChannels: 1,
        getChannelData: () => input,
      },
      outputBuffer: {
        numberOfChannels: 1,
        length: frames,
        getChannelData: () => output,
      },
    } as unknown as AudioProcessingEvent)
  }
}

const originalAudioContext = window.AudioContext

afterEach(() => {
  Object.defineProperty(window, 'AudioContext', {
    configurable: true,
    value: originalAudioContext,
  })
})

function installContext(harness: RenderHarness): void {
  Object.defineProperty(window, 'AudioContext', {
    configurable: true,
    value: class {
      constructor() { return harness }
    },
  })
}

describe('audio playout boundary', () => {
  it('reattaches the finite track for explicit autoplay recovery', async () => {
    const container = document.createElement('div')
    const blocked: boolean[] = []
    const boundary = new AudioPlaybackBoundary(container, (value) => blocked.push(value))
    const track = new FakeTrack()

    boundary.setTrack(track)
    await Promise.resolve()
    const first = container.firstElementChild
    boundary.suspend()
    expect(track.detachCount).toBe(1)
    expect(container.childElementCount).toBe(0)
    boundary.reset()
    await Promise.resolve()

    expect(track.attachCount).toBe(2)
    expect(track.detachCount).toBe(1)
    expect(container.firstElementChild).not.toBe(first)
    expect(blocked.at(-1)).toBe(false)

    boundary.clear()
    expect(track.detachCount).toBe(2)
    expect(container.childElementCount).toBe(0)
  })

  it('requires complete normalized counters and render frames before retirement', async () => {
    const harness = new RenderHarness()
    installContext(harness)
    const boundary = new AudioPlaybackBoundary(document.createElement('div'), vi.fn())
    const track = new FakeTrack()
    boundary.setTrack(track)
    await boundary.prepareFinitePlayout()
    track.receivedSamples = 960
    track.emittedSamples = 1
    const playout = boundary.waitForFinitePlayout(320, 16_000)
    await new Promise((resolve) => setTimeout(resolve, 25))
    let completed = false
    void playout.then(() => { completed = true })

    harness.render(959)
    await Promise.resolve()
    expect(completed).toBe(false)
    harness.render(1)
    await new Promise((resolve) => setTimeout(resolve, 25))
    expect(completed).toBe(false)

    track.emittedSamples = 960
    await playout
    expect(completed).toBe(true)
    expect(track.finiteTrack.readyState).toBe('live')
  })

  it('counts a short render before the first delivery-stats poll', async () => {
    const harness = new RenderHarness()
    installContext(harness)
    const boundary = new AudioPlaybackBoundary(document.createElement('div'), vi.fn())
    const track = new FakeTrack()
    boundary.setTrack(track)
    await boundary.prepareFinitePlayout()
    const playout = boundary.waitForFinitePlayout(320, 16_000)

    track.receivedSamples = 960
    track.emittedSamples = 960
    harness.render(960)
    track.end()

    await expect(playout).resolves.toBeUndefined()
  })

  it('does not confirm retirement before the correlated finite track has ended', async () => {
    const harness = new RenderHarness()
    installContext(harness)
    const boundary = new AudioPlaybackBoundary(document.createElement('div'), vi.fn())
    const track = new FakeTrack()
    boundary.setTrack(track)
    await boundary.prepareFinitePlayout()
    track.receivedSamples = 960
    track.emittedSamples = 960
    const playout = boundary.waitForFinitePlayout(320, 16_000)
    harness.render(960)
    await playout
    const ended = boundary.waitForFiniteTrackEnd()
    let completed = false
    void ended.then(() => { completed = true })

    await new Promise((resolve) => setTimeout(resolve, 25))
    expect(completed).toBe(false)
    track.end()
    await ended
    expect(completed).toBe(true)
  })

  it('does not accept transport progress while the render context is suspended', async () => {
    const harness = new RenderHarness()
    installContext(harness)
    const boundary = new AudioPlaybackBoundary(document.createElement('div'), vi.fn())
    const track = new FakeTrack()
    boundary.setTrack(track)
    await boundary.prepareFinitePlayout()
    track.receivedSamples = 960
    track.emittedSamples = 960
    const playout = boundary.waitForFinitePlayout(320, 16_000)
    await new Promise((resolve) => setTimeout(resolve, 25))
    let completed = false
    void playout.then(() => { completed = true })

    harness.state = 'suspended'
    harness.render(960)
    await Promise.resolve()
    expect(completed).toBe(false)
    harness.state = 'running'
    harness.render(960)
    track.end()
    await playout
  })

  it('fails closed when the delivered timeline contains concealed audio', async () => {
    const harness = new RenderHarness()
    installContext(harness)
    const boundary = new AudioPlaybackBoundary(document.createElement('div'), vi.fn())
    const track = new FakeTrack()
    boundary.setTrack(track)
    await boundary.prepareFinitePlayout()
    const playout = boundary.waitForFinitePlayout(320, 16_000)
    const rejected = expect(playout).rejects.toThrow('concealment')
    track.receivedSamples = 960
    track.emittedSamples = 960
    track.concealedSamples = 1
    await new Promise((resolve) => setTimeout(resolve, 25))

    harness.render(960)

    await rejected
  })

  it('closes every context across repeated session disposal', async () => {
    const contexts: RenderHarness[] = []
    Object.defineProperty(window, 'AudioContext', {
      configurable: true,
      value: class {
        constructor() {
          const context = new RenderHarness()
          contexts.push(context)
          return context
        }
      },
    })

    for (let index = 0; index < 2; index += 1) {
      const boundary = new AudioPlaybackBoundary(document.createElement('div'), vi.fn())
      boundary.setTrack(new FakeTrack())
      await boundary.dispose()
    }

    expect(contexts).toHaveLength(2)
    expect(contexts.every((context) => context.close.mock.calls.length === 1)).toBe(true)
  })

  it('closes the session context only on disposal and retries a failed close', async () => {
    const harness = new RenderHarness()
    harness.close
      .mockRejectedValueOnce(new Error('close failed'))
      .mockImplementationOnce(async () => { harness.state = 'closed' })
    installContext(harness)
    const boundary = new AudioPlaybackBoundary(document.createElement('div'), vi.fn())
    boundary.setTrack(new FakeTrack())

    boundary.clear()
    expect(harness.close).not.toHaveBeenCalled()
    await expect(boundary.dispose()).rejects.toThrow('disposal failed')
    expect(harness.close).toHaveBeenCalledTimes(1)
    await expect(boundary.dispose()).resolves.toBeUndefined()
    expect(harness.close).toHaveBeenCalledTimes(2)
  })
})
