export interface AttachableAudioTrack {
  attach(): HTMLMediaElement
  detach(element?: HTMLMediaElement): HTMLMediaElement[]
}

export class AudioPlaybackBoundary {
  private track: AttachableAudioTrack | null = null
  private element: HTMLMediaElement | null = null
  private pendingDetach: { track: AttachableAudioTrack | null; element: HTMLMediaElement } | null = null
  private generation = 0

  constructor(
    private readonly container: HTMLElement,
    private readonly onBlocked: (blocked: boolean) => void,
  ) {}

  setTrack(track: AttachableAudioTrack): void {
    this.clear()
    this.track = track
    this.attachFresh()
  }

  reset(): void {
    if (this.track === null) return
    this.detachElement()
    this.attachFresh()
  }

  suspend(): void {
    this.detachElement()
    this.onBlocked(false)
  }

  async confirmDrain(drainMs: number, timeoutMs: number): Promise<boolean> {
    const deadline = performance.now() + timeoutMs
    let element: HTMLMediaElement | null = null
    let generation = 0
    let startTime = 0
    while (performance.now() < deadline) {
      if (this.element !== element || this.generation !== generation) {
        element = this.element
        generation = this.generation
        startTime = element?.currentTime ?? 0
      }
      if (element !== null && element.currentTime - startTime >= drainMs / 1000) return true
      await new Promise((resolve) => setTimeout(resolve, 20))
    }
    return false
  }

  clear(): void {
    this.detachElement()
    this.track = null
    this.onBlocked(false)
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
        if (generation === this.generation) this.onBlocked(false)
      },
      () => {
        if (generation === this.generation) this.onBlocked(true)
      },
    )
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
