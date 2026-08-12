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

})
