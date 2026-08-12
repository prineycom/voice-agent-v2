import { describe, expect, it, vi } from 'vitest'
import { AudioPlaybackBoundary, type AttachableAudioTrack } from './playback'

class FiniteMediaTrack extends EventTarget {
  readonly id = 'finite-track'
  readyState: MediaStreamTrackState = 'live'

  end(): void {
    this.readyState = 'ended'
    this.dispatchEvent(new Event('ended'))
  }
}

class FakeTrack implements AttachableAudioTrack {
  attachCount = 0
  detachCount = 0
  receivedSamples = 0
  emittedSamples = 0
  readonly finiteTrack = new FiniteMediaTrack()
  readonly mediaStreamTrack = this.finiteTrack as unknown as MediaStreamTrack
  readonly elements: HTMLMediaElement[] = []

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

  async getRTCStatsReport(): Promise<RTCStatsReport> {
    return new Map([[
      'audio',
      {
        type: 'inbound-rtp',
        kind: 'audio',
        trackIdentifier: this.finiteTrack.id,
        totalSamplesReceived: this.receivedSamples,
        jitterBufferEmittedCount: this.emittedSamples,
      },
    ]]) as unknown as RTCStatsReport
  }
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
    expect(container.childElementCount).toBe(1)
    expect(container.firstElementChild).not.toBe(first)
    expect(container.querySelector('audio')?.dataset.voiceAgentAudio).toBe('agent-response')
    expect(blocked.at(-1)).toBe(false)

    boundary.clear()
    expect(track.detachCount).toBe(2)
    expect(container.childElementCount).toBe(0)
  })

  it('counts leading silence from the correlated inbound playout counters', async () => {
    const boundary = new AudioPlaybackBoundary(document.createElement('div'), vi.fn())
    const track = new FakeTrack()
    boundary.setTrack(track)
    await boundary.prepareFinitePlayout()
    const playout = boundary.waitForFinitePlayout(320, 16_000)
    let completed = false
    void playout.then(() => { completed = true })

    track.receivedSamples = 320
    track.emittedSamples = 320
    await new Promise((resolve) => setTimeout(resolve, 25))
    expect(completed).toBe(false)

    track.finiteTrack.end()
    await playout
    expect(completed).toBe(true)
  })

  it('waits for buffered late tail after track end and rejects partial delivery', async () => {
    const boundary = new AudioPlaybackBoundary(document.createElement('div'), vi.fn())
    const track = new FakeTrack()
    boundary.setTrack(track)
    await boundary.prepareFinitePlayout()
    const lateTail = boundary.waitForFinitePlayout(320, 16_000)
    let completed = false
    void lateTail.then(() => { completed = true })

    track.receivedSamples = 160
    track.emittedSamples = 160
    track.finiteTrack.end()
    await new Promise((resolve) => setTimeout(resolve, 25))
    expect(completed).toBe(false)
    track.receivedSamples = 320
    track.emittedSamples = 320
    await lateTail

    const partialTrack = new FakeTrack()
    boundary.setTrack(partialTrack)
    await boundary.prepareFinitePlayout()
    const partial = boundary.waitForFinitePlayout(320, 16_000)
    partialTrack.receivedSamples = 160
    partialTrack.emittedSamples = 320
    partialTrack.finiteTrack.end()
    await new Promise((resolve) => setTimeout(resolve, 25))
    boundary.clear()
    await expect(partial).rejects.toThrow('invalidated')
  })

  it('does not accept concealed gap samples as delivered response PCM', async () => {
    const boundary = new AudioPlaybackBoundary(document.createElement('div'), vi.fn())
    const track = new FakeTrack()
    boundary.setTrack(track)
    await boundary.prepareFinitePlayout()
    const playout = boundary.waitForFinitePlayout(320, 16_000)
    let completed = false
    void playout.then(() => { completed = true })

    track.receivedSamples = 300
    track.emittedSamples = 320
    track.finiteTrack.end()
    await new Promise((resolve) => setTimeout(resolve, 25))
    expect(completed).toBe(false)
    track.receivedSamples = 320
    await playout
  })

  it('surfaces and resumes the same Web Audio context used for rendering', async () => {
    const original = window.AudioContext
    let state: AudioContextState = 'suspended'
    const resume = vi.fn()
      .mockRejectedValueOnce(new Error('autoplay blocked'))
      .mockImplementationOnce(async () => { state = 'running' })
    const node = {
      onaudioprocess: null,
      disconnect: vi.fn(),
    } as unknown as ScriptProcessorNode
    class FakeAudioContext {
      get state(): AudioContextState { return state }
      resume = resume
      createScriptProcessor = vi.fn().mockReturnValue(node)
    }
    Object.defineProperty(window, 'AudioContext', {
      configurable: true,
      value: FakeAudioContext,
    })
    const blocked: boolean[] = []
    const boundary = new AudioPlaybackBoundary(
      document.createElement('div'),
      (value) => blocked.push(value),
    )
    const track = new FakeTrack() as FakeTrack & {
      setAudioContext(context: AudioContext | undefined): void
      setWebAudioPlugins(nodes: AudioNode[]): void
    }
    track.setAudioContext = vi.fn()
    track.setWebAudioPlugins = vi.fn()

    try {
      boundary.setTrack(track)
      await Promise.resolve()
      expect(blocked.at(-1)).toBe(true)

      await boundary.resume()

      expect(resume).toHaveBeenCalledTimes(2)
      expect(blocked.at(-1)).toBe(false)
      expect(track.setAudioContext).toHaveBeenCalledOnce()
    } finally {
      boundary.clear()
      Object.defineProperty(window, 'AudioContext', {
        configurable: true,
        value: original,
      })
    }
  })
})
