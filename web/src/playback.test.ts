import { describe, expect, it, vi } from 'vitest'
import { AudioPlaybackBoundary, type AttachableAudioTrack } from './playback'

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

describe('persistent playback observation', () => {
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
