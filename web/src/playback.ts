export interface AttachableAudioTrack {
  attach(): HTMLMediaElement
  detach(element?: HTMLMediaElement): HTMLMediaElement[]
  readonly mediaStreamTrack?: MediaStreamTrack
}

export interface SpeechEnvelopeObservation {
  level: number
  observedAtMs: number
}

/** Converts decoded signed-around-128 time-domain samples into a bounded RMS envelope. */
export function speechEnvelopeFromTimeDomain(samples: Uint8Array): number {
  if (samples.length === 0) return 0
  let squared = 0
  for (const sample of samples) {
    const normalized = (sample - 128) / 128
    squared += normalized * normalized
  }
  return Math.min(1, Math.sqrt(squared / samples.length) * 3.2)
}

const ENVELOPE_INTERVAL_MS = 33

export class AudioPlaybackBoundary {
  private track: AttachableAudioTrack | null = null
  private element: HTMLMediaElement | null = null
  private pendingDetach: { track: AttachableAudioTrack | null; element: HTMLMediaElement } | null = null
  private attachmentGeneration = 0
  private publicationGeneration = 0
  private elementBlocked = false
  private audioContext: AudioContext | null = null
  private analyserSource: MediaElementAudioSourceNode | null = null
  private analyser: AnalyserNode | null = null
  private analyserSamples: Uint8Array<ArrayBuffer> | null = null
  private envelopeFrame: number | null = null
  private lastEnvelopeAtMs = -Infinity
  private playbackActive = false
  private envelopeGraphDisabled = false
  private playbackListeners: Array<{
    element: HTMLMediaElement
    type: string
    listener: EventListener
  }> = []

  constructor(
    private readonly container: HTMLElement,
    private readonly onBlocked: (blocked: boolean) => void,
    private readonly onSpeechEnvelope: (observation: SpeechEnvelopeObservation) => void = () => undefined,
  ) {}

  setTrack(track: AttachableAudioTrack, publicationGeneration = this.publicationGeneration + 1): void {
    if (publicationGeneration < this.publicationGeneration) return
    if (
      publicationGeneration === this.publicationGeneration
      && this.track === track
      && this.element !== null
    ) return
    this.clear()
    this.publicationGeneration = publicationGeneration
    this.track = track
    this.envelopeGraphDisabled = false
    this.attachFresh()
  }

  async resume(): Promise<void> {
    const element = this.element
    if (element === null) return
    try {
      await element.play()
      this.elementBlocked = false
      if (this.elementCanProduceOutput(element)) this.playbackActive = true
      this.startEnvelopeObservation()
    } catch (error) {
      this.deactivatePlayback()
      this.elementBlocked = true
      this.reportBlocked()
      throw error
    }
    this.reportBlocked()
  }

  reset(): void {
    if (this.track === null) return
    this.detachElement()
    this.envelopeGraphDisabled = false
    this.attachFresh()
  }

  suspend(publicationGeneration?: number): boolean {
    if (
      publicationGeneration !== undefined
      && publicationGeneration !== this.publicationGeneration
    ) return false
    this.detachElement()
    this.elementBlocked = false
    this.reportBlocked()
    return true
  }

  clear(): void {
    const errors: unknown[] = []
    try {
      this.detachElement()
    } catch (error) {
      errors.push(error)
    }
    this.element = null
    this.elementBlocked = false
    this.stopEnvelopeObservation()
    this.reportBlocked()
    if (errors.length === 0) {
      this.track = null
      this.pendingDetach = null
      this.publicationGeneration = 0
    }
    if (errors.length > 0) throw new AggregateError(errors, 'audio playback cleanup failed')
  }

  async dispose(): Promise<void> {
    this.clear()
  }

  private attachFresh(): void {
    if (this.track === null) return
    const generation = ++this.attachmentGeneration
    const element = this.track.attach()
    element.autoplay = true
    element.controls = false
    element.dataset.voiceAgentAudio = 'agent-response'
    this.container.replaceChildren(element)
    this.element = element
    this.bindPlaybackEvents(element, generation)
    void element.play().then(
      () => {
        if (generation === this.attachmentGeneration) {
          this.elementBlocked = false
          if (this.elementCanProduceOutput(element)) this.playbackActive = true
          this.startEnvelopeObservation()
          this.reportBlocked()
        }
      },
      () => {
        if (generation === this.attachmentGeneration) {
          this.deactivatePlayback()
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
    this.attachmentGeneration += 1
    this.removePlaybackListeners()
    this.stopEnvelopeObservation()
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
    if (errors.length > 0) throw new AggregateError(errors, 'audio playback cleanup failed')
  }

  private bindPlaybackEvents(element: HTMLMediaElement, generation: number): void {
    const add = (type: string, listener: EventListener): void => {
      element.addEventListener(type, listener)
      this.playbackListeners.push({ element, type, listener })
    }
    add('playing', () => {
      if (generation !== this.attachmentGeneration || this.element !== element) return
      this.playbackActive = true
      this.startEnvelopeObservation()
    })
    const deactivate = (): void => {
      if (generation !== this.attachmentGeneration || this.element !== element) return
      this.deactivatePlayback()
    }
    for (const type of ['pause', 'ended', 'waiting', 'stalled', 'emptied', 'abort', 'error']) {
      add(type, deactivate)
    }
  }

  private removePlaybackListeners(): void {
    for (const { element, type, listener } of this.playbackListeners) {
      element.removeEventListener(type, listener)
    }
    this.playbackListeners = []
    this.playbackActive = false
  }

  private elementCanProduceOutput(element: HTMLMediaElement): boolean {
    return !element.paused && !element.ended && element.readyState >= 2 && element.error === null
  }

  private deactivatePlayback(): void {
    if (!this.playbackActive) return
    this.playbackActive = false
    this.onSpeechEnvelope({ level: 0, observedAtMs: performance.now() })
  }

  private startEnvelopeObservation(): void {
    const element = this.element
    if (!this.playbackActive || element === null || this.envelopeGraphDisabled) return
    if (typeof AudioContext === 'undefined') return
    if (this.audioContext === null) {
      let context: AudioContext | null = null
      let analyser: AnalyserNode | null = null
      let source: MediaElementAudioSourceNode | null = null
      let elementWasRerouted = false
      try {
        context = new AudioContext()
        analyser = context.createAnalyser()
        analyser.fftSize = 256
        analyser.smoothingTimeConstant = 0.45
        source = context.createMediaElementSource(element)
        elementWasRerouted = true
        source.connect(analyser)
        analyser.connect(context.destination)
        this.audioContext = context
        this.analyserSource = source
        this.analyser = analyser
        this.analyserSamples = new Uint8Array(analyser.fftSize)
      } catch {
        try {
          source?.disconnect()
        } catch {}
        try {
          analyser?.disconnect()
        } catch {}
        if (context !== null) void context.close().catch(() => undefined)
        this.envelopeGraphDisabled = true
        if (elementWasRerouted) this.restoreDirectPlayback(element)
        return
      }
    }
    const context = this.audioContext
    void context.resume().then(
      () => {
        if (
          this.audioContext === context && this.playbackActive
          && this.element === element && this.envelopeFrame === null
        ) {
          this.envelopeFrame = requestAnimationFrame(this.observeSpeechEnvelope)
        }
      },
      () => {
        if (this.audioContext === context && this.element === element) {
          this.envelopeGraphDisabled = true
          this.restoreDirectPlayback(element)
        }
      },
    )
  }

  private restoreDirectPlayback(element: HTMLMediaElement): void {
    if (this.element !== element) return
    try {
      this.detachElement()
      this.elementBlocked = false
      this.attachFresh()
    } catch {
      this.elementBlocked = true
      this.reportBlocked()
    }
  }

  private readonly observeSpeechEnvelope = (timeMs: number): void => {
    const analyser = this.analyser
    const samples = this.analyserSamples
    const element = this.element
    if (analyser === null || samples === null || element === null || this.elementBlocked) {
      this.envelopeFrame = null
      return
    }
    if (this.playbackActive && !this.elementCanProduceOutput(element)) {
      this.deactivatePlayback()
    }
    if (this.playbackActive && timeMs - this.lastEnvelopeAtMs >= ENVELOPE_INTERVAL_MS) {
      analyser.getByteTimeDomainData(samples)
      this.lastEnvelopeAtMs = timeMs
      this.onSpeechEnvelope({
        level: speechEnvelopeFromTimeDomain(samples),
        observedAtMs: timeMs,
      })
    }
    this.envelopeFrame = requestAnimationFrame(this.observeSpeechEnvelope)
  }

  private stopEnvelopeObservation(): void {
    if (this.envelopeFrame !== null) cancelAnimationFrame(this.envelopeFrame)
    this.envelopeFrame = null
    this.lastEnvelopeAtMs = -Infinity
    this.playbackActive = false
    try {
      this.analyserSource?.disconnect()
    } catch {}
    try {
      this.analyser?.disconnect()
    } catch {}
    this.analyserSource = null
    this.analyser = null
    this.analyserSamples = null
    const context = this.audioContext
    this.audioContext = null
    if (context !== null) void context.close().catch(() => undefined)
    this.onSpeechEnvelope({ level: 0, observedAtMs: performance.now() })
  }
}
