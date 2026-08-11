import { afterEach, describe, expect, it, vi } from 'vitest'
import { AudioPlaybackBoundary, type AttachableAudioTrack } from './playback'

class FakeTrack implements AttachableAudioTrack {
  attachCount = 0
  detachCount = 0

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
}

afterEach(() => vi.useRealTimers())

describe('audio playout boundary', () => {
  it('reattaches the live track to drain browser-side stale audio', async () => {
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

  it('confirms drain only after the attached media clock advances', async () => {
    vi.useFakeTimers()
    const container = document.createElement('div')
    const boundary = new AudioPlaybackBoundary(container, vi.fn())
    boundary.setTrack(new FakeTrack())
    const element = container.querySelector('audio') as HTMLAudioElement

    const confirmation = boundary.confirmDrain(250, 1_000)
    await vi.advanceTimersByTimeAsync(200)
    expect(await Promise.race([confirmation, Promise.resolve('pending')])).toBe('pending')
    Object.defineProperty(element, 'currentTime', { value: 0.3, configurable: true })
    await vi.advanceTimersByTimeAsync(20)

    await expect(confirmation).resolves.toBe(true)
  })

  it('restarts drain confirmation after autoplay recovery reattaches audio', async () => {
    vi.useFakeTimers()
    const container = document.createElement('div')
    const boundary = new AudioPlaybackBoundary(container, vi.fn())
    boundary.setTrack(new FakeTrack())

    const confirmation = boundary.confirmDrain(250, 1_000)
    await vi.advanceTimersByTimeAsync(900)
    boundary.reset()
    const recoveredElement = container.querySelector('audio') as HTMLAudioElement
    await vi.advanceTimersByTimeAsync(20)
    Object.defineProperty(recoveredElement, 'currentTime', { value: 0.3, configurable: true })
    await vi.advanceTimersByTimeAsync(20)

    await expect(confirmation).resolves.toBe(true)
  })
})
