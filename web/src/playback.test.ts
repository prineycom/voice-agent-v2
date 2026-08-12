import { describe, expect, it, vi } from 'vitest'
import { AudioPlaybackBoundary, type AttachableAudioTrack } from './playback'

class FiniteMediaTrack extends EventTarget {
  readonly id: string
  readyState: MediaStreamTrackState = 'live'

  constructor(id = 'finite-track') {
    super()
    this.id = id
  }
}

class FakeTrack implements AttachableAudioTrack {
  attachCount = 0
  detachCount = 0
  readonly finiteTrack: FiniteMediaTrack
  readonly mediaStreamTrack: MediaStreamTrack

  constructor(id = 'finite-track') {
    this.finiteTrack = new FiniteMediaTrack(id)
    this.mediaStreamTrack = this.finiteTrack as unknown as MediaStreamTrack
  }

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

describe('audio publication boundary', () => {
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

  it('acknowledges only the exact live attached finite publication', async () => {
    const boundary = new AudioPlaybackBoundary(document.createElement('div'), vi.fn())
    const first = new FakeTrack('track-one')
    boundary.setTrack(first)
    await boundary.prepareFinitePlayout()

    await expect(boundary.waitForFinitePlayout(320, 16_000)).resolves.toBeUndefined()

    const replacement = new FakeTrack('track-two')
    boundary.setTrack(replacement)
    await expect(boundary.waitForFinitePlayout(320, 16_000)).rejects.toThrow(
      'publication boundary',
    )
  })

  it('does not use pre-audio callbacks or sample counters as playout proof', async () => {
    const boundary = new AudioPlaybackBoundary(document.createElement('div'), vi.fn())
    const track = new FakeTrack()
    boundary.setTrack(track)
    await boundary.prepareFinitePlayout()

    await expect(boundary.waitForFinitePlayout(1, 16_000)).resolves.toBeUndefined()
    expect(track.attachCount).toBe(1)
  })

  it('fails when the correlated track is no longer live', async () => {
    const boundary = new AudioPlaybackBoundary(document.createElement('div'), vi.fn())
    const track = new FakeTrack()
    boundary.setTrack(track)
    await boundary.prepareFinitePlayout()
    track.finiteTrack.readyState = 'ended'

    await expect(boundary.waitForFinitePlayout(320, 16_000)).rejects.toThrow(
      'publication boundary',
    )
  })

  it('rejects an invalid sealed finite-media declaration', async () => {
    const boundary = new AudioPlaybackBoundary(document.createElement('div'), vi.fn())
    boundary.setTrack(new FakeTrack())
    await boundary.prepareFinitePlayout()

    await expect(boundary.waitForFinitePlayout(0, 16_000)).rejects.toThrow(
      'publication boundary',
    )
    await expect(boundary.waitForFinitePlayout(320, 48_000)).rejects.toThrow(
      'publication boundary',
    )
  })

  it('retries cleanup after detach failure', async () => {
    const boundary = new AudioPlaybackBoundary(document.createElement('div'), vi.fn())
    const track = new FakeTrack()
    const detach = vi.spyOn(track, 'detach')
      .mockImplementationOnce(() => { throw new Error('detach failed') })
      .mockReturnValueOnce([])
    boundary.setTrack(track)

    await expect(boundary.dispose()).rejects.toThrow('cleanup failed')
    await expect(boundary.dispose()).resolves.toBeUndefined()
    expect(detach).toHaveBeenCalledTimes(2)
  })
})
