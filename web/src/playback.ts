export interface AttachableAudioTrack {
  attach(): HTMLMediaElement
  detach(element?: HTMLMediaElement): HTMLMediaElement[]
  readonly mediaStreamTrack?: MediaStreamTrack
  getRTCStatsReport?(): Promise<RTCStatsReport | undefined>
  setAudioContext?(context: AudioContext | undefined): void
  setWebAudioPlugins?(nodes: AudioNode[]): void
}

interface PlayoutWaiter {
  targetSamples: number
  resolve: () => void
  reject: (error: Error) => void
}

interface AudioPlayoutStats {
  receivedSamples: number
  emittedSamples: number
}

export class AudioPlaybackBoundary {
  private track: AttachableAudioTrack | null = null
  private element: HTMLMediaElement | null = null
  private pendingDetach: { track: AttachableAudioTrack | null; element: HTMLMediaElement } | null = null
  private generation = 0
  private finiteTrackEnded = false
  private statsBaseline: AudioPlayoutStats | null = null
  private latestStats: AudioPlayoutStats | null = null
  private stopTrackEndObservation: (() => void) | null = null
  private statsPoll: ReturnType<typeof setTimeout> | null = null
  private statsReadActive = false
  private renderNode: ScriptProcessorNode | null = null
  private audioContext: AudioContext | null = null
  private elementBlocked = false
  private contextBlocked = false
  private playoutWaiters = new Set<PlayoutWaiter>()

  constructor(
    private readonly container: HTMLElement,
    private readonly onBlocked: (blocked: boolean) => void,
  ) {}

  setTrack(track: AttachableAudioTrack): void {
    this.clear()
    this.track = track
    this.configureAudioContext(track)
    this.observeFiniteTrack(track)
    this.attachFresh()
  }

  async prepareFinitePlayout(): Promise<void> {
    const stats = await this.readAudioStats()
    if (stats === null) {
      throw new Error('finite audio statistics boundary is unavailable')
    }
    this.statsBaseline = stats
    this.latestStats = stats
  }

  waitForFinitePlayout(sampleCount: number, sampleRate: number): Promise<void> {
    if (
      !Number.isSafeInteger(sampleCount)
      || sampleCount < 1
      || !Number.isSafeInteger(sampleRate)
      || sampleRate !== 16_000
      || this.track?.mediaStreamTrack === undefined
      || this.track.getRTCStatsReport === undefined
      || this.statsBaseline === null
      || this.element === null
    ) return Promise.reject(new Error('finite audio playout boundary is unavailable'))
    if (this.playoutReached(sampleCount)) return Promise.resolve()
    return new Promise<void>((resolve, reject) => {
      this.playoutWaiters.add({ targetSamples: sampleCount, resolve, reject })
      this.scheduleStatsPoll(0)
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
      this.contextBlocked = String(context.state) !== 'running'
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
    this.rejectPlayoutWaiters()
    this.statsBaseline = null
    this.latestStats = null
    this.detachElement()
    this.attachFresh()
  }

  suspend(): void {
    this.rejectPlayoutWaiters()
    this.statsBaseline = null
    this.latestStats = null
    this.detachElement()
    this.elementBlocked = false
    this.reportBlocked()
  }

  clear(): void {
    const errors: unknown[] = []
    this.rejectPlayoutWaiters()
    this.stopTrackEndObservation?.()
    this.stopTrackEndObservation = null
    this.stopStatsPoll()
    if (this.renderNode !== null) {
      this.renderNode.onaudioprocess = null
      try {
        this.renderNode.disconnect()
      } catch (error) {
        errors.push(error)
      }
      this.renderNode = null
    }
    if (this.track !== null && this.audioContext !== null) {
      try {
        this.track.setWebAudioPlugins?.([])
        this.track.setAudioContext?.(undefined)
      } catch (error) {
        errors.push(error)
      }
    }
    try {
      this.detachElement()
    } catch (error) {
      errors.push(error)
    } finally {
      this.track = null
      this.element = null
      this.finiteTrackEnded = false
      this.statsBaseline = null
      this.latestStats = null
      this.elementBlocked = false
      this.contextBlocked = false
      this.reportBlocked()
    }
    if (errors.length > 0) {
      throw new AggregateError(errors, 'audio playback cleanup failed')
    }
  }

  private configureAudioContext(track: AttachableAudioTrack): void {
    if (
      track.setAudioContext === undefined
      || track.setWebAudioPlugins === undefined
      || typeof window.AudioContext !== 'function'
    ) return
    const context = this.audioContext ?? new window.AudioContext({ latencyHint: 'interactive' })
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
    }
    track.setAudioContext(context)
    track.setWebAudioPlugins([node])
    this.renderNode = node
  }

  private observeFiniteTrack(track: AttachableAudioTrack): void {
    const mediaTrack = track.mediaStreamTrack
    if (mediaTrack === undefined) return
    this.finiteTrackEnded = mediaTrack.readyState === 'ended'
    const ended = () => {
      this.finiteTrackEnded = true
      this.scheduleStatsPoll(0)
    }
    mediaTrack.addEventListener('ended', ended)
    this.stopTrackEndObservation = () => mediaTrack.removeEventListener('ended', ended)
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

  private async readAudioStats(): Promise<AudioPlayoutStats | null> {
    const track = this.track
    if (track?.getRTCStatsReport === undefined) return null
    const report = await track.getRTCStatsReport()
    if (report === undefined) return null
    const mediaTrackId = track.mediaStreamTrack?.id
    let receivedSamples: unknown
    let emittedSamples: unknown
    report.forEach((raw) => {
      const stat = raw as unknown as Record<string, unknown>
      if (
        stat.type !== 'inbound-rtp'
        || (stat.kind !== 'audio' && stat.mediaType !== 'audio')
        || (mediaTrackId !== undefined
          && typeof stat.trackIdentifier === 'string'
          && stat.trackIdentifier !== mediaTrackId)
      ) return
      receivedSamples = stat.totalSamplesReceived
      emittedSamples = stat.jitterBufferEmittedCount
    })
    if (
      typeof receivedSamples !== 'number'
      || !Number.isFinite(receivedSamples)
      || receivedSamples < 0
      || typeof emittedSamples !== 'number'
      || !Number.isFinite(emittedSamples)
      || emittedSamples < 0
    ) return null
    return { receivedSamples, emittedSamples }
  }

  private playoutReached(targetSamples: number): boolean {
    const baseline = this.statsBaseline
    const latest = this.latestStats
    return (
      this.finiteTrackEnded
      && baseline !== null
      && latest !== null
      && latest.receivedSamples - baseline.receivedSamples >= targetSamples
      && latest.emittedSamples - baseline.emittedSamples >= targetSamples
    )
  }

  private scheduleStatsPoll(delayMs: number): void {
    if (this.statsPoll !== null || this.statsReadActive) return
    if (this.playoutWaiters.size === 0) return
    this.statsPoll = setTimeout(() => {
      this.statsPoll = null
      void this.pollStats()
    }, delayMs)
  }

  private async pollStats(): Promise<void> {
    if (this.statsReadActive || this.playoutWaiters.size === 0) return
    this.statsReadActive = true
    try {
      const stats = await this.readAudioStats()
      if (stats === null || this.statsBaseline === null) {
        this.rejectPlayoutWaiters('finite audio statistics boundary was lost')
        return
      }
      this.latestStats = stats
      for (const waiter of this.playoutWaiters) {
        if (!this.playoutReached(waiter.targetSamples)) continue
        this.playoutWaiters.delete(waiter)
        waiter.resolve()
      }
    } catch {
      this.rejectPlayoutWaiters('finite audio statistics boundary failed')
      return
    } finally {
      this.statsReadActive = false
    }
    if (this.playoutWaiters.size > 0) this.scheduleStatsPoll(20)
  }

  private stopStatsPoll(): void {
    if (this.statsPoll === null) return
    clearTimeout(this.statsPoll)
    this.statsPoll = null
  }

  private rejectPlayoutWaiters(message = 'audio playout boundary was invalidated'): void {
    for (const waiter of this.playoutWaiters) {
      waiter.reject(new Error(message))
    }
    this.playoutWaiters.clear()
    this.stopStatsPoll()
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
