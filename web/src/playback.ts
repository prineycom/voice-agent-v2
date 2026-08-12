export interface AttachableAudioTrack {
  attach(): HTMLMediaElement
  detach(element?: HTMLMediaElement): HTMLMediaElement[]
  setAudioContext?(context: AudioContext | undefined): void
  setWebAudioPlugins?(nodes: AudioNode[]): void
  observeRenderedSamples?(
    observer: (sampleCount: number, sampleRate: number) => void,
  ): () => void
}

interface RenderWaiter {
  targetSeconds: number
  resolve: () => void
  reject: (error: Error) => void
}

export class AudioPlaybackBoundary {
  private track: AttachableAudioTrack | null = null
  private element: HTMLMediaElement | null = null
  private pendingDetach: { track: AttachableAudioTrack | null; element: HTMLMediaElement } | null = null
  private generation = 0
  private renderedSeconds = 0
  private renderObservable = false
  private stopRenderObserver: (() => void) | null = null
  private renderNode: ScriptProcessorNode | null = null
  private audioContext: AudioContext | null = null
  private renderWaiters = new Set<RenderWaiter>()

  constructor(
    private readonly container: HTMLElement,
    private readonly onBlocked: (blocked: boolean) => void,
  ) {}

  setTrack(track: AttachableAudioTrack): void {
    this.clear()
    this.track = track
    this.configureRenderObserver(track)
    this.attachFresh()
  }

  armRenderBoundary(): void {
    if (!this.renderObservable) {
      throw new Error('audio render boundary is unavailable')
    }
    this.rejectRenderWaiters()
    this.renderedSeconds = 0
  }

  waitForRenderedSamples(sampleCount: number, sampleRate: number): Promise<void> {
    if (
      !Number.isSafeInteger(sampleCount)
      || sampleCount < 1
      || !Number.isSafeInteger(sampleRate)
      || sampleRate < 8_000
      || sampleRate > 192_000
      || !this.renderObservable
    ) return Promise.reject(new Error('audio render boundary is unavailable'))
    const targetSeconds = sampleCount / sampleRate
    if (this.renderedSeconds >= targetSeconds) return Promise.resolve()
    return new Promise<void>((resolve, reject) => {
      this.renderWaiters.add({ targetSeconds, resolve, reject })
    })
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

  clear(): void {
    this.rejectRenderWaiters()
    this.stopRenderObserver?.()
    this.stopRenderObserver = null
    if (this.renderNode !== null) {
      this.renderNode.onaudioprocess = null
      try {
        this.renderNode.disconnect()
      } catch {}
      this.renderNode = null
    }
    if (this.track !== null && this.audioContext !== null) {
      try {
        this.track.setWebAudioPlugins?.([])
        this.track.setAudioContext?.(undefined)
      } catch {}
    }
    this.renderedSeconds = 0
    this.renderObservable = false
    this.detachElement()
    this.track = null
    this.onBlocked(false)
  }

  private configureRenderObserver(track: AttachableAudioTrack): void {
    if (track.observeRenderedSamples !== undefined) {
      this.renderObservable = true
      this.stopRenderObserver = track.observeRenderedSamples((samples, sampleRate) => {
        this.recordRenderedSamples(samples, sampleRate)
      })
      return
    }
    if (
      track.setAudioContext === undefined
      || track.setWebAudioPlugins === undefined
      || typeof window.AudioContext !== 'function'
    ) return
    const context = this.audioContext ?? new window.AudioContext({ latencyHint: 'interactive' })
    this.audioContext = context
    const node = context.createScriptProcessor(1024, 1, 1)
    node.onaudioprocess = (event) => {
      const input = event.inputBuffer
      const output = event.outputBuffer
      for (let channel = 0; channel < output.numberOfChannels; channel += 1) {
        const outputData = output.getChannelData(channel)
        const inputData = input.numberOfChannels > channel
          ? input.getChannelData(channel)
          : null
        if (inputData === null) outputData.fill(0)
        else outputData.set(inputData)
      }
      if (input.numberOfChannels > 0) {
        this.recordRenderedSamples(input.length, input.sampleRate)
      }
    }
    track.setAudioContext(context)
    track.setWebAudioPlugins([node])
    this.renderNode = node
    this.renderObservable = true
  }

  private recordRenderedSamples(sampleCount: number, sampleRate: number): void {
    if (
      !Number.isFinite(sampleCount)
      || sampleCount <= 0
      || !Number.isFinite(sampleRate)
      || sampleRate <= 0
    ) return
    this.renderedSeconds += sampleCount / sampleRate
    for (const waiter of this.renderWaiters) {
      if (this.renderedSeconds < waiter.targetSeconds) continue
      this.renderWaiters.delete(waiter)
      waiter.resolve()
    }
  }

  private rejectRenderWaiters(): void {
    for (const waiter of this.renderWaiters) {
      waiter.reject(new Error('audio render boundary was invalidated'))
    }
    this.renderWaiters.clear()
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
