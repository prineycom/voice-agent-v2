import {
  manifestIsCompatible,
  validateAvatarControl,
  type AvatarControlInputV1,
  type AvatarHealthV1,
  type AvatarModuleFactoryV1,
  type AvatarModuleV1,
  type ValidatedAvatarControlV1,
} from './contract'

export type AvatarHealthListener = (health: AvatarHealthV1) => void

/**
 * Owns module compatibility, input validation, lifecycle and an explicitly
 * reported static fallback. It never owns renderer geometry or frames.
 */
export class AvatarHostV1 {
  private container: HTMLElement | null = null
  private activeModule: AvatarModuleV1 | null = null
  private activeFactoryIndex = -1
  private lastTimestampMs = -1
  private lastControl: ValidatedAvatarControlV1 | null = null
  private rejectedInputs = 0
  private renderFailures = 0
  private readonly healthListeners = new Set<AvatarHealthListener>()

  constructor(
    private readonly factories: readonly AvatarModuleFactoryV1[],
    private readonly onHealth?: AvatarHealthListener,
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
    if (!this.activateFirstCompatibleModule(0)) {
      this.publishHealth()
      throw new Error('no compatible avatar module could be mounted')
    }
  }

  update(input: AvatarControlInputV1): void {
    if (this.container === null || this.activeModule === null) return
    const control = validateAvatarControl(input)
    this.rejectedInputs += control.rejectedSignals
    if (control.timestampMs < this.lastTimestampMs) {
      this.rejectedInputs += 1
      this.publishHealth()
      return
    }
    this.lastTimestampMs = control.timestampMs
    this.lastControl = control
    try {
      this.activeModule.update(control)
    } catch {
      this.renderFailures += 1
      this.failOver(control)
    }
    this.publishHealth()
  }

  cancel(timestampMs: number): void {
    if (this.activeModule === null || !Number.isFinite(timestampMs) || timestampMs < 0) {
      this.rejectedInputs += 1
      this.publishHealth()
      return
    }
    try {
      this.activeModule.cancel(timestampMs)
    } catch {
      this.renderFailures += 1
      if (this.lastControl !== null) this.failOver(this.lastControl)
    }
    this.publishHealth()
  }

  health(): AvatarHealthV1 {
    const mounted = this.activeModule !== null
    return {
      status: !mounted ? 'failed' : this.activeFactoryIndex === 0 ? 'ready' : 'degraded',
      activeModuleId: this.activeModule?.manifest.id ?? null,
      usingFallback: this.activeFactoryIndex > 0,
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
      this.lastControl = null
      this.lastTimestampMs = -1
      this.publishHealth()
    }
  }

  private failOver(control: ValidatedAvatarControlV1): void {
    const nextFactory = this.activeFactoryIndex + 1
    try {
      this.activeModule?.dispose()
    } catch {
      this.renderFailures += 1
    }
    this.activeModule = null
    this.container?.replaceChildren()
    if (!this.activateFirstCompatibleModule(nextFactory)) return
    const fallback = this.activeModule as AvatarModuleV1 | null
    try {
      fallback?.update(control)
    } catch {
      this.renderFailures += 1
      try {
        fallback?.dispose()
      } finally {
        this.activeModule = null
        this.container?.replaceChildren()
      }
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
    this.onHealth?.(health)
    for (const listener of this.healthListeners) listener(health)
  }
}
