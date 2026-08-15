import {
  AVATAR_HOST_INTERFACE_VERSION,
  AVATAR_REQUIRED_CAPABILITIES,
  type AvatarModuleV1,
  type ValidatedAvatarControlV1,
} from './contract'

/** Explicitly reported no-motion fallback used only after the selected renderer fails. */
export class StaticFallbackModule implements AvatarModuleV1 {
  readonly manifest = {
    interfaceVersion: AVATAR_HOST_INTERFACE_VERSION,
    id: 'static-eye-fallback-v1',
    displayName: 'Static Eye Fallback',
    capabilities: AVATAR_REQUIRED_CAPABILITIES,
    deterministic: true,
  } as const

  private root: HTMLDivElement | null = null

  setFailureHandler(): void {}

  mount(container: HTMLElement): void {
    const root = document.createElement('div')
    root.className = 'avatar-static-fallback'
    root.setAttribute('role', 'img')
    root.setAttribute('aria-label', 'Static avatar fallback')
    const ring = document.createElement('div')
    ring.className = 'avatar-static-fallback__ring'
    const pupil = document.createElement('div')
    pupil.className = 'avatar-static-fallback__pupil'
    ring.append(pupil)
    root.append(ring)
    container.replaceChildren(root)
    this.root = root
  }

  update(input: ValidatedAvatarControlV1): void {
    if (this.root === null) throw new Error('static avatar fallback is not mounted')
    this.root.dataset.state = input.lifecycle
  }

  cancel(): void {
    if (this.root !== null) this.root.dataset.state = 'idle'
  }

  dispose(): void {
    this.root?.remove()
    this.root = null
  }
}

export function createStaticFallbackModule(): AvatarModuleV1 {
  return new StaticFallbackModule()
}
