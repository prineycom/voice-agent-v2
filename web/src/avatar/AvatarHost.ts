import {
  avatarMotionPreferenceIsValid,
  avatarTimestampIsAdmissible,
  manifestIsCompatible,
  validateAvatarControl,
  type AvatarHealthV1,
  type AvatarModuleFactoryV1,
  type AvatarModuleV1,
  type AvatarMotionPreference,
  type ValidatedAvatarControlV1,
} from './contract'

export type AvatarHealthListener = (health: AvatarHealthV1) => void

type AvatarCommand =
  | { kind: 'update'; control: ValidatedAvatarControlV1 }
  | { kind: 'cancel'; timestampMs: number; motion: AvatarMotionPreference }

/**
 * Owns module compatibility, input validation, lifecycle and renderer health.
 * It never owns renderer geometry or frames.
 */
export class AvatarHostV1 {
  private container: HTMLElement | null = null
  private activeModule: AvatarModuleV1 | null = null
  private activeFactoryIndex = -1
  private lastTimestampMs = -1
  private rejectedInputs = 0
  private inputDegraded = false
  private renderFailures = 0
  private lastCommand: AvatarCommand | null = null
  private lastPublishedHealth: AvatarHealthV1 | null = null
  private readonly healthListeners = new Set<AvatarHealthListener>()

  constructor(
    private readonly factories: readonly AvatarModuleFactoryV1[],
    private readonly onHealth?: AvatarHealthListener,
    private readonly now: () => number = () => performance.now(),
  ) {
    if (factories.length === 0) throw new Error('avatar host requires at least one module factory')
  }

  subscribeHealth(listener: AvatarHealthListener): () => void {
    this.healthListeners.add(listener)
    listener(this.health())
    return () => { this.healthListeners.delete(listener) }
  }

  mount(container: HTMLElement): void {
    if (this.container !== null) throw new Error('avatar host is already mounted')
    this.container = container
    if (!this.activateFirstCompatibleModule(0)) this.publishHealth()
  }

  update(input: unknown): void {
    if (this.container === null || this.activeModule === null) return
    const result = validateAvatarControl(input, this.now())
    if (!result.accepted) {
      this.rejectedInputs += result.rejectedSignals
      this.inputDegraded = true
      this.publishHealth()
      return
    }
    const control = result.control
    this.rejectedInputs += control.rejectedSignals
    this.inputDegraded = control.rejectedSignals > 0
    if (control.timestampMs < this.lastTimestampMs) {
      this.rejectedInputs += 1
      this.inputDegraded = true
      this.publishHealth()
      return
    }
    this.lastTimestampMs = control.timestampMs
    this.lastCommand = { kind: 'update', control }
    try {
      this.activeModule.update(control)
    } catch {
      this.renderFailures += 1
      this.failOverUpdate(control)
    }
    this.publishHealth()
  }

  cancel(timestampMs: number, motion: AvatarMotionPreference): void {
    if (
      this.activeModule === null
      || !avatarTimestampIsAdmissible(timestampMs, this.now())
      || !avatarMotionPreferenceIsValid(motion)
      || timestampMs < this.lastTimestampMs
    ) {
      this.rejectedInputs += 1
      this.inputDegraded = true
      this.publishHealth()
      return
    }
    this.inputDegraded = false
    this.lastTimestampMs = timestampMs
    this.lastCommand = { kind: 'cancel', timestampMs, motion }
    try {
      this.activeModule.cancel(timestampMs, motion)
    } catch {
      this.renderFailures += 1
      this.failOverCancellation(timestampMs, motion)
    }
    this.publishHealth()
  }

  health(): AvatarHealthV1 {
    const mounted = this.activeModule !== null
    return {
      status: !mounted
        ? 'failed'
        : this.activeFactoryIndex === 0 && !this.inputDegraded
          ? 'ready'
          : 'degraded',
      activeModuleId: this.activeModule?.manifest.id ?? null,
      usingFallback: mounted && this.activeFactoryIndex > 0,
      rejectedInputs: this.rejectedInputs,
      renderFailures: this.renderFailures,
    }
  }

  dispose(): void {
    try {
      this.activeModule?.dispose()
    } finally {
      this.activeModule = null
      this.activeFactoryIndex = -1
      this.container?.replaceChildren()
      this.container = null
      this.lastTimestampMs = -1
      this.lastCommand = null
      this.inputDegraded = false
      this.publishHealth()
    }
  }

  private switchToNextModule(): AvatarModuleV1 | null {
    const nextFactory = this.activeFactoryIndex + 1
    try {
      this.activeModule?.dispose()
    } catch {
      this.renderFailures += 1
    }
    this.activeModule = null
    this.activeFactoryIndex = -1
    this.container?.replaceChildren()
    return this.activateFirstCompatibleModule(nextFactory) ? this.activeModule : null
  }

  private failOverUpdate(control: ValidatedAvatarControlV1): void {
    const fallback = this.switchToNextModule()
    try {
      fallback?.update(control)
    } catch {
      this.disableFailedFallback(fallback)
    }
  }

  private failOverCancellation(timestampMs: number, motion: AvatarMotionPreference): void {
    const fallback = this.switchToNextModule()
    try {
      fallback?.cancel(timestampMs, motion)
    } catch {
      this.disableFailedFallback(fallback)
    }
  }

  private handleModuleFailure(module: AvatarModuleV1): void {
    if (module !== this.activeModule) return
    this.renderFailures += 1
    const fallback = this.switchToNextModule()
    try {
      if (this.lastCommand?.kind === 'update') fallback?.update(this.lastCommand.control)
      if (this.lastCommand?.kind === 'cancel') {
        fallback?.cancel(this.lastCommand.timestampMs, this.lastCommand.motion)
      }
    } catch {
      this.disableFailedFallback(fallback)
    }
    this.publishHealth()
  }

  private disableFailedFallback(fallback: AvatarModuleV1 | null): void {
    this.renderFailures += 1
    try {
      fallback?.dispose()
    } catch {
      this.renderFailures += 1
    } finally {
      this.activeModule = null
      this.activeFactoryIndex = -1
      this.container?.replaceChildren()
    }
  }

  private activateFirstCompatibleModule(startIndex: number): boolean {
    if (this.container === null) return false
    for (let index = startIndex; index < this.factories.length; index += 1) {
      let candidate: AvatarModuleV1 | null = null
      try {
        candidate = this.factories[index]()
        if (!manifestIsCompatible(candidate.manifest)) {
          this.rejectedInputs += 1
          candidate.dispose()
          continue
        }
        const failureSource = candidate
        candidate.setFailureHandler(() => this.handleModuleFailure(failureSource))
        candidate.mount(this.container)
        this.activeModule = candidate
        this.activeFactoryIndex = index
        this.publishHealth()
        return true
      } catch {
        this.renderFailures += 1
        try {
          candidate?.dispose()
        } catch {
          this.renderFailures += 1
        }
        this.container.replaceChildren()
      }
    }
    return false
  }

  private publishHealth(): void {
    const health = this.health()
    const previous = this.lastPublishedHealth
    if (
      previous !== null
      && previous.status === health.status
      && previous.activeModuleId === health.activeModuleId
      && previous.usingFallback === health.usingFallback
      && previous.rejectedInputs === health.rejectedInputs
      && previous.renderFailures === health.renderFailures
    ) return
    this.lastPublishedHealth = health
    this.onHealth?.(health)
    for (const listener of this.healthListeners) listener(health)
  }
}
