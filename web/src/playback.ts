export interface AttachableAudioTrack {
  attach(): HTMLMediaElement
  detach(element?: HTMLMediaElement): HTMLMediaElement[]
  readonly mediaStreamTrack?: MediaStreamTrack
  getRTCStatsReport?(): Promise<RTCStatsReport | undefined>
  setAudioContext?(context: AudioContext | undefined): void
  setWebAudioPlugins?(nodes: AudioNode[]): void
}

interface PlayoutWaiter {
  targetRenderFrames: number
  targetDeliverySampleCount: number
  resolve: () => void
  reject: (error: Error) => void
}

interface DeliveryStats {
  receivedSamples: number
  emittedSamples: number
  concealedSamples: number
}

export class AudioPlaybackBoundary {
  private track: AttachableAudioTrack | null = null
  private element: HTMLMediaElement | null = null
  private pendingDetach: { track: AttachableAudioTrack | null; element: HTMLMediaElement } | null = null
  private generation = 0
  private renderNode: ScriptProcessorNode | null = null
  private audioContext: AudioContext | null = null
  private renderFrames = 0
  private renderBaseline: number | null = null
  private renderSampleRate: number | null = null
  private renderArmed = false
  private renderStarted = false
  private boundaryGeneration = 0
  private boundaryTrack: MediaStreamTrack | null = null
  private boundaryTrackEnded = false
  private boundaryTrackEndedListener: (() => void) | null = null
  private deliveryBaseline: DeliveryStats | null = null
  private deliveryPoll: ReturnType<typeof setTimeout> | null = null
  private deliveryReadActive = false
  private elementBlocked = false
  private contextBlocked = false
  private playoutWaiters = new Set<PlayoutWaiter>()
  private trackEndWaiters = new Set<PlayoutWaiter>()

  constructor(
    private readonly container: HTMLElement,
    private readonly onBlocked: (blocked: boolean) => void,
  ) {}

  setTrack(track: AttachableAudioTrack): void {
    this.clear()
    this.track = track
    this.configureAudioContext(track)
    this.attachFresh()
  }

  async prepareFinitePlayout(): Promise<void> {
    const context = this.audioContext
    const track = this.track
    const mediaTrack = track?.mediaStreamTrack
    const generation = this.boundaryGeneration
    if (
      context === null
      || this.renderNode === null
      || this.element === null
      || context.state !== 'running'
      || !Number.isFinite(context.sampleRate)
      || context.sampleRate < 1
      || mediaTrack === undefined
      || mediaTrack.readyState !== 'live'
      || mediaTrack.id.length < 1
    ) throw new Error('finite Web Audio render boundary is unavailable')
    const deliveryBaseline = await this.readDeliveryStats(mediaTrack)
    if (
      deliveryBaseline === null
      || generation !== this.boundaryGeneration
      || context !== this.audioContext
      || track !== this.track
      || mediaTrack !== this.track?.mediaStreamTrack
      || mediaTrack.readyState !== 'live'
      || this.element === null
    ) throw new Error('finite audio delivery boundary is unavailable')
    this.boundaryGeneration += 1
    const boundaryGeneration = this.boundaryGeneration
    this.renderBaseline = this.renderFrames
    this.renderSampleRate = context.sampleRate
    this.deliveryBaseline = deliveryBaseline
    this.boundaryTrack = mediaTrack
    this.boundaryTrackEnded = false
    this.boundaryTrackEndedListener = () => {
      if (
        boundaryGeneration === this.boundaryGeneration
        && this.boundaryTrack === mediaTrack
        && mediaTrack.readyState === 'ended'
      ) {
        this.boundaryTrackEnded = true
        for (const waiter of this.trackEndWaiters) waiter.resolve()
        this.trackEndWaiters.clear()
        this.scheduleDeliveryValidation(0)
      }
    }
    mediaTrack.addEventListener('ended', this.boundaryTrackEndedListener)
    if (mediaTrack.readyState !== 'live') {
      this.invalidateRenderBoundary()
      throw new Error('finite audio delivery boundary is unavailable')
    }
    this.renderArmed = true
    this.renderStarted = false
    this.scheduleDeliveryValidation(0)
  }

  waitForFinitePlayout(sampleCount: number, sampleRate: number): Promise<void> {
    const context = this.audioContext
    const baseline = this.renderBaseline
    const renderSampleRate = this.renderSampleRate
    if (
      !Number.isSafeInteger(sampleCount)
      || sampleCount < 1
      || !Number.isSafeInteger(sampleRate)
      || sampleRate !== 16_000
      || context === null
      || context.state !== 'running'
      || baseline === null
      || renderSampleRate === null
      || this.renderNode === null
      || this.element === null
      || !this.renderArmed
      || this.deliveryBaseline === null
      || this.boundaryTrack === null
    ) return Promise.reject(new Error('finite Web Audio render boundary is unavailable'))
    const normalizedSampleCount = Math.ceil(
      sampleCount * renderSampleRate / sampleRate,
    )
    const targetRenderFrames = baseline + normalizedSampleCount
    return new Promise<void>((resolve, reject) => {
      this.playoutWaiters.add({
        targetRenderFrames,
        targetDeliverySampleCount: normalizedSampleCount,
        resolve,
        reject,
      })
      if (this.renderFrames >= targetRenderFrames) this.scheduleDeliveryValidation(0)
    })
  }

  waitForFiniteTrackEnd(): Promise<void> {
    const mediaTrack = this.boundaryTrack
    if (
      !this.renderArmed
      || mediaTrack === null
      || mediaTrack !== this.track?.mediaStreamTrack
    ) return Promise.reject(new Error('finite audio track boundary is unavailable'))
    if (this.boundaryTrackEnded && mediaTrack.readyState === 'ended') {
      return Promise.resolve()
    }
    if (mediaTrack.readyState !== 'live') {
      return Promise.reject(new Error('finite audio track boundary was lost'))
    }
    return new Promise<void>((resolve, reject) => {
      this.trackEndWaiters.add({
        targetRenderFrames: 0,
        targetDeliverySampleCount: 0,
        resolve,
        reject,
      })
    })
  }

  async resume(): Promise<void> {
    const context = this.audioContext
    if (context !== null && context.state !== 'running') {
      try {
        await context.resume()
      } catch (error) {
        this.contextBlocked = true
        this.reportBlocked()
        throw error
      }
      const resumedState = String(context.state)
      this.contextBlocked = resumedState !== 'running'
      this.reportBlocked()
      if (this.contextBlocked) throw new Error('audio context remains blocked')
    }
    const element = this.element
    if (element !== null) {
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
  }

  reset(): void {
    if (this.track === null) return
    this.invalidateRenderBoundary()
    this.detachElement()
    this.attachFresh()
  }

  suspend(): void {
    this.invalidateRenderBoundary()
    this.detachElement()
    this.elementBlocked = false
    this.reportBlocked()
  }

  clear(): void {
    const errors: unknown[] = []
    this.invalidateRenderBoundary()
    const track = this.track
    if (this.renderNode !== null) {
      this.renderNode.onaudioprocess = null
    }
    if (track !== null && this.audioContext !== null) {
      try {
        track.setWebAudioPlugins?.([])
      } catch (error) {
        errors.push(error)
      }
      try {
        track.setAudioContext?.(undefined)
      } catch (error) {
        errors.push(error)
      }
    }
    if (this.renderNode !== null) {
      try {
        this.renderNode.disconnect()
      } catch (error) {
        errors.push(error)
      }
      this.renderNode = null
    }
    try {
      this.detachElement()
    } catch (error) {
      errors.push(error)
    }
    this.element = null
    this.elementBlocked = false
    this.contextBlocked = false
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
    const errors: unknown[] = []
    try {
      this.clear()
    } catch (error) {
      errors.push(error)
    }
    const context = this.audioContext
    if (context !== null) {
      try {
        if (context.state !== 'closed') await context.close()
        if (this.audioContext === context) this.audioContext = null
      } catch (error) {
        errors.push(error)
      }
    }
    if (errors.length > 0) {
      throw new AggregateError(errors, 'audio playback disposal failed')
    }
  }

  private configureAudioContext(track: AttachableAudioTrack): void {
    if (
      track.setAudioContext === undefined
      || track.setWebAudioPlugins === undefined
      || typeof window.AudioContext !== 'function'
    ) return
    const context = this.audioContext ?? new window.AudioContext({ latencyHint: 'interactive' })
    if (context.state === 'closed') throw new Error('audio context is closed')
    this.audioContext = context
    this.contextBlocked = context.state !== 'running'
    this.reportBlocked()
    if (this.contextBlocked) {
      void context.resume().then(
        () => {
          this.contextBlocked = context.state !== 'running'
          this.reportBlocked()
        },
        () => {
          this.contextBlocked = true
          this.reportBlocked()
        },
      )
    }
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
      const mediaTrack = this.track?.mediaStreamTrack
      if (
        this.renderArmed
        && context.state === 'running'
        && mediaTrack?.readyState === 'live'
        && !mediaTrack.muted
      ) {
        this.renderFrames += output.length
        this.scheduleDeliveryValidation(0)
      }
    }
    track.setAudioContext(context)
    track.setWebAudioPlugins([node])
    this.renderNode = node
  }

  private async readDeliveryStats(mediaTrack: MediaStreamTrack): Promise<DeliveryStats | null> {
    const track = this.track
    if (
      track?.getRTCStatsReport === undefined
      || track.mediaStreamTrack !== mediaTrack
      || mediaTrack.id.length < 1
    ) return null
    const report = await track.getRTCStatsReport()
    if (report === undefined) return null
    const matches: DeliveryStats[] = []
    report.forEach((raw) => {
      const stat = raw as unknown as Record<string, unknown>
      if (
        stat.type !== 'inbound-rtp'
        || (stat.kind !== 'audio' && stat.mediaType !== 'audio')
        || stat.trackIdentifier !== mediaTrack.id
      ) return
      const receivedSamples = stat.totalSamplesReceived
      const emittedSamples = stat.jitterBufferEmittedCount
      const concealedSamples = stat.concealedSamples ?? 0
      if (
        typeof receivedSamples !== 'number'
        || !Number.isSafeInteger(receivedSamples)
        || receivedSamples < 0
        || typeof emittedSamples !== 'number'
        || !Number.isSafeInteger(emittedSamples)
        || emittedSamples < 0
        || typeof concealedSamples !== 'number'
        || !Number.isSafeInteger(concealedSamples)
        || concealedSamples < 0
      ) return
      matches.push({ receivedSamples, emittedSamples, concealedSamples })
    })
    return matches.length === 1 ? matches[0] : null
  }

  private scheduleDeliveryValidation(delayMs: number): void {
    if (this.deliveryPoll !== null || this.deliveryReadActive) return
    if (!this.renderArmed) return
    if (
      this.renderStarted
      && ![...this.playoutWaiters].some(
        (waiter) => this.renderFrames >= waiter.targetRenderFrames,
      )
    ) return
    const generation = this.boundaryGeneration
    this.deliveryPoll = setTimeout(() => {
      this.deliveryPoll = null
      void this.validateDelivery(generation)
    }, delayMs)
  }

  private async validateDelivery(generation: number): Promise<void> {
    if (
      this.deliveryReadActive
      || !this.renderArmed
      || (this.renderStarted && this.playoutWaiters.size === 0)
    ) return
    this.deliveryReadActive = true
    try {
      const baseline = this.deliveryBaseline
      const mediaTrack = this.boundaryTrack
      const latest = mediaTrack === null ? null : await this.readDeliveryStats(mediaTrack)
      if (generation === this.boundaryGeneration) {
        if (
          baseline === null
          || latest === null
          || mediaTrack === null
          || mediaTrack !== this.track?.mediaStreamTrack
          || this.audioContext?.state !== 'running'
        ) {
          this.rejectPlayoutWaiters('finite audio delivery boundary was lost')
        } else if (
          latest.receivedSamples < baseline.receivedSamples
          || latest.emittedSamples < baseline.emittedSamples
          || latest.concealedSamples < baseline.concealedSamples
        ) {
          this.renderArmed = false
          this.rejectPlayoutWaiters('finite audio delivery counters regressed')
        } else if (latest.concealedSamples > baseline.concealedSamples) {
          this.renderArmed = false
          this.rejectPlayoutWaiters('finite audio delivery required concealment')
        } else {
          if (latest.emittedSamples > baseline.emittedSamples) this.renderStarted = true
          for (const waiter of this.playoutWaiters) {
            if (
              this.renderFrames < waiter.targetRenderFrames
              || latest.receivedSamples - baseline.receivedSamples
                < waiter.targetDeliverySampleCount
              || latest.emittedSamples - baseline.emittedSamples
                < waiter.targetDeliverySampleCount
            ) continue
            this.playoutWaiters.delete(waiter)
            waiter.resolve()
          }
        }
      }
    } catch {
      if (generation === this.boundaryGeneration) {
        this.rejectPlayoutWaiters('finite audio delivery boundary failed')
      }
    } finally {
      this.deliveryReadActive = false
    }
    if (this.renderArmed && (!this.renderStarted || this.playoutWaiters.size > 0)) {
      this.scheduleDeliveryValidation(20)
    }
  }

  private rejectPlayoutWaiters(message: string): void {
    for (const waiter of this.playoutWaiters) waiter.reject(new Error(message))
    this.playoutWaiters.clear()
    for (const waiter of this.trackEndWaiters) waiter.reject(new Error(message))
    this.trackEndWaiters.clear()
    if (this.deliveryPoll !== null) {
      clearTimeout(this.deliveryPoll)
      this.deliveryPoll = null
    }
  }

  private invalidateRenderBoundary(): void {
    this.boundaryGeneration += 1
    if (this.boundaryTrack !== null && this.boundaryTrackEndedListener !== null) {
      this.boundaryTrack.removeEventListener('ended', this.boundaryTrackEndedListener)
    }
    this.boundaryTrack = null
    this.boundaryTrackEnded = false
    this.boundaryTrackEndedListener = null
    this.renderArmed = false
    this.renderStarted = false
    this.renderBaseline = null
    this.renderSampleRate = null
    this.deliveryBaseline = null
    this.rejectPlayoutWaiters('audio playout boundary was invalidated')
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
    this.onBlocked(this.elementBlocked || this.contextBlocked)
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
