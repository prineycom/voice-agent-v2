export interface AttachableAudioTrack {
  attach(): HTMLMediaElement
  detach(element?: HTMLMediaElement): HTMLMediaElement[]
}

export class AudioPlaybackBoundary {
  private track: AttachableAudioTrack | null = null
  private element: HTMLMediaElement | null = null
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
      if (element === null && this.element !== null) {
        element = this.element
        generation = this.generation
        startTime = element.currentTime
      }
      if (
        element !== null
        && this.element === element
        && this.generation === generation
        && element.currentTime - startTime >= drainMs / 1000
      ) return true
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
    if (this.element === null) return
    this.track?.detach(this.element)
    this.element.pause()
    this.element.removeAttribute('src')
    this.element.load()
    this.element.remove()
    this.element = null
    this.container.replaceChildren()
  }
}
