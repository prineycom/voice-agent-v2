import { describe, expect, it, vi } from 'vitest'
import { AvatarHostV1 } from './AvatarHost'
import { MvpEyeModule } from './eye/MvpEyeModule'
import {
  AVATAR_CONTROL_SCHEMA_VERSION,
  AVATAR_HOST_INTERFACE_VERSION,
  AVATAR_REQUIRED_CAPABILITIES,
  validateAvatarControl,
  type AvatarModuleV1,
  type ValidatedAvatarControlV1,
} from './contract'

function module(
  id: string,
  hooks: Partial<AvatarModuleV1> = {},
): AvatarModuleV1 {
  return {
    manifest: {
      interfaceVersion: AVATAR_HOST_INTERFACE_VERSION,
      id,
      displayName: id,
      capabilities: AVATAR_REQUIRED_CAPABILITIES,
      deterministic: true,
    },
    setFailureHandler: vi.fn(),
    mount: vi.fn(),
    update: vi.fn(),
    cancel: vi.fn(),
    dispose: vi.fn(),
    ...hooks,
  }
}

function input(timestampMs = 1_000) {
  return {
    schemaVersion: AVATAR_CONTROL_SCHEMA_VERSION,
    timestampMs,
    idleSeed: 17,
    lifecycle: 'speaking' as const,
    motion: 'full' as const,
    trackingTarget: { x: 0.4, y: -0.2, confidence: 0.8, observedAtMs: timestampMs - 10 },
    speechEnvelope: { level: 0.7, observedAtMs: timestampMs - 5, source: 'decoded-playout' as const },
  }
}

const HOST_NOW_MS = 2_000
const now = () => HOST_NOW_MS

describe('avatar host v1 boundary', () => {
  it('rejects malformed/stale optional signals and delivers only safe defaults', () => {
    const result = validateAvatarControl({
      ...input(),
      trackingTarget: { x: 4, y: 0, confidence: 1, observedAtMs: 0 },
      speechEnvelope: { level: 2, observedAtMs: 1_000, source: 'decoded-playout' },
    }, HOST_NOW_MS)

    expect(result.accepted).toBe(true)
    if (!result.accepted) throw new Error('valid control was rejected')
    expect(result.control.trackingTarget).toBeNull()
    expect(result.control.speechEnvelope).toBeNull()
    expect(result.control.rejectedSignals).toBe(2)
  })

  it('drops incompatible schemas and malformed top-level controls without throwing', () => {
    const renderer = module('primary')
    const host = new AvatarHostV1([() => renderer], undefined, now)
    host.mount(document.createElement('div'))

    expect(validateAvatarControl(null, HOST_NOW_MS).accepted).toBe(false)
    host.update(null)
    host.update({ ...input(), schemaVersion: 'voice-agent.avatar-control.v2' })
    host.update({ ...input(), unexpected: true })

    expect(renderer.update).not.toHaveBeenCalled()
    expect(host.health().rejectedInputs).toBe(3)
  })

  it('reports an explicit degraded fallback when the selected renderer fails', () => {
    const primary = module('primary', { update: vi.fn(() => { throw new Error('render failed') }) })
    const fallback = module('fallback')
    const host = new AvatarHostV1([() => primary, () => fallback], undefined, now)
    host.mount(document.createElement('div'))
    host.update(input())

    expect(primary.dispose).toHaveBeenCalledTimes(1)
    expect(fallback.update).toHaveBeenCalledWith(expect.objectContaining({
      lifecycle: 'speaking',
      speechEnvelope: expect.objectContaining({ level: 0.7, source: 'decoded-playout' }),
    }) satisfies Partial<ValidatedAvatarControlV1>)
    expect(host.health()).toMatchObject({
      status: 'degraded', activeModuleId: 'fallback', usingFallback: true, renderFailures: 1,
    })
  })

  it('admits updates and cancellation through one monotonic timestamp boundary', () => {
    const renderer = module('primary')
    const host = new AvatarHostV1([() => renderer], undefined, now)
    host.mount(document.createElement('div'))
    host.update(input(1_000))
    host.cancel(999)
    host.cancel(1_010)
    host.update(input(1_005))

    expect(renderer.update).toHaveBeenCalledTimes(1)
    expect(renderer.cancel).toHaveBeenCalledTimes(1)
    expect(renderer.cancel).toHaveBeenCalledWith(1_010)
    expect(host.health().rejectedInputs).toBe(2)
  })

  it('rejects future timestamps without poisoning later update or cancellation admission', () => {
    const renderer = module('primary')
    const host = new AvatarHostV1([() => renderer], undefined, now)
    host.mount(document.createElement('div'))

    host.update(input(1e308))
    host.cancel(1e308)
    host.update(input(1_000))
    host.cancel(1_010)

    expect(renderer.update).toHaveBeenCalledTimes(1)
    expect(renderer.cancel).toHaveBeenCalledWith(1_010)
    expect(host.health().rejectedInputs).toBe(2)
  })

  it('propagates cancellation to the reported fallback when the renderer throws', () => {
    const primary = module('primary', { cancel: vi.fn(() => { throw new Error('cancel failed') }) })
    const fallback = module('fallback')
    const host = new AvatarHostV1([() => primary, () => fallback], undefined, now)
    host.mount(document.createElement('div'))
    host.update(input(1_000))
    host.cancel(1_010)

    expect(fallback.update).not.toHaveBeenCalled()
    expect(fallback.cancel).toHaveBeenCalledWith(1_010)
    expect(host.health()).toMatchObject({
      status: 'degraded', activeModuleId: 'fallback', usingFallback: true, renderFailures: 1,
    })
  })

  it('fails over and replays the latest command when a module reports an asynchronous loop failure', () => {
    const failureReport: { current: (() => void) | null } = { current: null }
    const primary = module('primary', {
      setFailureHandler: vi.fn((handler) => {
        failureReport.current = handler === null ? null : () => handler({ kind: 'render-loop-failed' })
      }),
    })
    const fallback = module('fallback')
    const host = new AvatarHostV1([() => primary, () => fallback], undefined, now)
    host.mount(document.createElement('div'))
    host.update(input(1_000))

    expect(failureReport.current).not.toBeNull()
    failureReport.current?.()

    expect(primary.dispose).toHaveBeenCalledTimes(1)
    expect(fallback.update).toHaveBeenCalledWith(expect.objectContaining({
      timestampMs: 1_000,
      lifecycle: 'speaking',
    }))
    expect(host.health()).toMatchObject({
      status: 'degraded', activeModuleId: 'fallback', usingFallback: true, renderFailures: 1,
    })
  })

  it('publishes health only when an observable health field changes', () => {
    const renderer = module('primary')
    const onHealth = vi.fn()
    const host = new AvatarHostV1([() => renderer], onHealth, now)
    host.mount(document.createElement('div'))
    host.update(input(1_000))
    host.update(input(1_010))

    expect(onHealth).toHaveBeenCalledTimes(1)
  })

  it('reports animation-frame draw failures instead of silently stopping its loop', () => {
    const scheduled: { frame: FrameRequestCallback | null } = { frame: null }
    const requestFrame = vi.fn((callback: FrameRequestCallback) => {
      scheduled.frame = callback
      return 1
    })
    vi.stubGlobal('requestAnimationFrame', requestFrame)
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
    const renderer = new MvpEyeModule()
    const onFailure = vi.fn()
    renderer.setFailureHandler(onFailure)
    const container = document.createElement('div')

    try {
      renderer.mount(container)
      const result = validateAvatarControl(input(), HOST_NOW_MS)
      if (!result.accepted) throw new Error('valid control was rejected')
      renderer.update(result.control)
      const root = container.querySelector<HTMLElement>('.mvp-eye')
      const frame = scheduled.frame
      if (root === null || frame === null) throw new Error('eye renderer did not mount or schedule')
      vi.spyOn(root, 'setAttribute').mockImplementation(() => { throw new Error('draw failed') })

      frame(HOST_NOW_MS)

      expect(onFailure).toHaveBeenCalledWith({ kind: 'render-loop-failed' })
      expect(requestFrame).toHaveBeenCalledTimes(1)
    } finally {
      renderer.dispose()
      vi.unstubAllGlobals()
    }
  })

  it('refuses a module that does not provide the complete versioned capability set', () => {
    const incompatible = module('old')
    Object.assign(incompatible, {
      manifest: { ...incompatible.manifest, capabilities: ['lifecycle'] },
    })
    const fallback = module('compatible')
    const host = new AvatarHostV1([() => incompatible, () => fallback], undefined, now)
    host.mount(document.createElement('div'))

    expect(host.health()).toMatchObject({
      activeModuleId: 'compatible', usingFallback: true, status: 'degraded',
    })
  })
})
