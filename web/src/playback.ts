export interface AttachableAudioTrack {
  attach(): HTMLMediaElement
  detach(element?: HTMLMediaElement): HTMLMediaElement[]
  readonly mediaStreamTrack?: MediaStreamTrack
}

export class AudioPlaybackBoundary {
  private track: AttachableAudioTrack | null = null
  private element: HTMLMediaElement | null = null
  private pendingDetach: { track: AttachableAudioTrack | null; element: HTMLMediaElement } | null = null
  private generation = 0
  private boundaryTrack: MediaStreamTrack | null = null
  private elementBlocked = false

  constructor(
    private readonly container: HTMLElement,
    private readonly onBlocked: (blocked: boolean) => void,
  ) {}

  setTrack(track: AttachableAudioTrack): void {
    this.clear()
    this.track = track
    this.attachFresh()
  }

  async prepareFinitePlayout(): Promise<void> {
    const mediaTrack = this.track?.mediaStreamTrack
    if (
      this.element === null
      || mediaTrack === undefined
      || mediaTrack.readyState !== 'live'
      || mediaTrack.id.length < 1
    ) throw new Error('finite audio publication boundary is unavailable')
    this.boundaryTrack = mediaTrack
  }

  waitForFinitePlayout(sampleCount: number, sampleRate: number): Promise<void> {
    const mediaTrack = this.track?.mediaStreamTrack
    if (
      !Number.isSafeInteger(sampleCount)
      || sampleCount < 1
      || !Number.isSafeInteger(sampleRate)
      || sampleRate !== 16_000
      || this.element === null
      || this.boundaryTrack === null
      || mediaTrack !== this.boundaryTrack
      || mediaTrack.readyState !== 'live'
    ) return Promise.reject(new Error('finite audio publication boundary is unavailable'))
    return Promise.resolve()
  }

  async resume(): Promise<void> {
    const element = this.element
    if (element === null) return
    try {
      await element.play()
      this.elementBlocked = false
    } catch (error) {
      this.elementBlocked = true
      this.reportBlocked()
      throw error
    }
    this.reportBlocked()
  }

  reset(): void {
    if (this.track === null) return
    this.invalidateBoundary()
    this.detachElement()
    this.attachFresh()
  }

  suspend(): void {
    this.invalidateBoundary()
    this.detachElement()
    this.elementBlocked = false
    this.reportBlocked()
  }

  clear(): void {
    this.invalidateBoundary()
    const errors: unknown[] = []
    try {
      this.detachElement()
    } catch (error) {
      errors.push(error)
    }
    this.element = null
    this.elementBlocked = false
    this.reportBlocked()
    if (errors.length === 0) {
      this.track = null
      this.pendingDetach = null
    }
    if (errors.length > 0) {
      throw new AggregateError(errors, 'audio playback cleanup failed')
    }
  }

  async dispose(): Promise<void> {
    this.clear()
  }

  private invalidateBoundary(): void {
    this.boundaryTrack = null
  }

  private attachFresh(): void {
    if (this.track === null) return
    const generation = ++this.generation
    const element = this.track.attach()
    element.autoplay = true
    element.controls = false
    element.dataset.voiceAgentAudio = 'agent-response'
    this.container.replaceChildren(element)
    this.element = element
    void element.play().then(
      () => {
        if (generation === this.generation) {
          this.elementBlocked = false
          this.reportBlocked()
        }
      },
      () => {
        if (generation === this.generation) {
          this.elementBlocked = true
          this.reportBlocked()
        }
      },
    )
  }

  private reportBlocked(): void {
    this.onBlocked(this.elementBlocked)
  }

  private detachElement(): void {
    this.generation += 1
    const pending = this.pendingDetach
    const track = pending?.track ?? this.track
    const element = pending?.element ?? this.element
    if (element === null) return
    const errors: unknown[] = []
    try {
      track?.detach(element)
    } catch (error) {
      errors.push(error)
    } finally {
      for (const shutdown of [
        () => element.pause(),
        () => element.removeAttribute('src'),
        () => element.load(),
        () => element.remove(),
        () => this.container.replaceChildren(),
      ]) {
        try {
          shutdown()
        } catch (error) {
          errors.push(error)
        }
      }
      if (this.element === element) this.element = null
      this.pendingDetach = errors.length === 0 ? null : { track, element }
    }
    if (errors.length > 0) {
      throw new AggregateError(errors, 'audio playback cleanup failed')
    }
  }
}
