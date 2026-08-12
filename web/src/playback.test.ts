import { afterEach, describe, expect, it, vi } from 'vitest'
import { AudioPlaybackBoundary, type AttachableAudioTrack } from './playback'

class FakeTrack implements AttachableAudioTrack {
  attachCount = 0
  detachCount = 0
  renderObserver: ((sampleCount: number, sampleRate: number, containsSignal?: boolean) => void) | null = null

  attach(): HTMLMediaElement {
    this.attachCount += 1
    const element = document.createElement('audio')
    element.play = vi.fn().mockResolvedValue(undefined)
    element.pause = vi.fn()
    element.load = vi.fn()
    return element
  }

  detach(): HTMLMediaElement[] {
    this.detachCount += 1
    return []
  }

  observeRenderedSamples(
    observer: (sampleCount: number, sampleRate: number, containsSignal?: boolean) => void,
  ): () => void {
    this.renderObserver = observer
    return () => {
      this.renderObserver = null
    }
  }
}

afterEach(() => vi.useRealTimers())

describe('audio playout boundary', () => {
  it('reattaches the live track for explicit autoplay recovery', async () => {
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

  it('ignores pre-RTP render silence and resolves at the correlated sample boundary', async () => {
    const boundary = new AudioPlaybackBoundary(
      document.createElement('div'),
      vi.fn(),
    )
    const track = new FakeTrack()
    boundary.setTrack(track)
    const rendered = boundary.waitForRenderedSamples(320, 16_000)
    let completed = false
    void rendered.then(() => { completed = true })

    track.renderObserver?.(10_000, 16_000, false)
    track.renderObserver?.(319, 16_000, true)
    await Promise.resolve()
    expect(completed).toBe(false)
    track.renderObserver?.(1, 16_000, true)
    await rendered

    expect(completed).toBe(true)
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
    track.observeRenderedSamples = undefined as never
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
