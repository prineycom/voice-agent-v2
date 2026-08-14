import { describe, expect, it, vi } from 'vitest'
import { AvatarHostV1 } from './AvatarHost'
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

describe('avatar host v1 boundary', () => {
  it('rejects malformed/stale optional signals and delivers only safe defaults', () => {
    const result = validateAvatarControl({
      ...input(),
      trackingTarget: { x: 4, y: 0, confidence: 1, observedAtMs: 0 },
      speechEnvelope: { level: 2, observedAtMs: 1_000, source: 'decoded-playout' },
    })

    expect(result.accepted).toBe(true)
    if (!result.accepted) throw new Error('valid control was rejected')
    expect(result.control.trackingTarget).toBeNull()
    expect(result.control.speechEnvelope).toBeNull()
    expect(result.control.rejectedSignals).toBe(2)
  })

  it('drops incompatible schemas and malformed top-level controls without throwing', () => {
    const renderer = module('primary')
    const host = new AvatarHostV1([() => renderer])
    host.mount(document.createElement('div'))

    expect(validateAvatarControl(null).accepted).toBe(false)
    host.update(null)
    host.update({ ...input(), schemaVersion: 'voice-agent.avatar-control.v2' })
    host.update({ ...input(), unexpected: true })

    expect(renderer.update).not.toHaveBeenCalled()
    expect(host.health().rejectedInputs).toBe(3)
  })

  it('reports an explicit degraded fallback when the selected renderer fails', () => {
    const primary = module('primary', { update: vi.fn(() => { throw new Error('render failed') }) })
    const fallback = module('fallback')
    const host = new AvatarHostV1([() => primary, () => fallback])
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
    const host = new AvatarHostV1([() => renderer])
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

  it('propagates cancellation to the reported fallback when the renderer throws', () => {
    const primary = module('primary', { cancel: vi.fn(() => { throw new Error('cancel failed') }) })
    const fallback = module('fallback')
    const host = new AvatarHostV1([() => primary, () => fallback])
    host.mount(document.createElement('div'))
    host.update(input(1_000))
    host.cancel(1_010)

    expect(fallback.update).not.toHaveBeenCalled()
    expect(fallback.cancel).toHaveBeenCalledWith(1_010)
    expect(host.health()).toMatchObject({
      status: 'degraded', activeModuleId: 'fallback', usingFallback: true, renderFailures: 1,
    })
  })

  it('publishes health only when an observable health field changes', () => {
    const renderer = module('primary')
    const onHealth = vi.fn()
    const host = new AvatarHostV1([() => renderer], onHealth)
    host.mount(document.createElement('div'))
    host.update(input(1_000))
    host.update(input(1_010))

    expect(onHealth).toHaveBeenCalledTimes(1)
  })

  it('refuses a module that does not provide the complete versioned capability set', () => {
    const incompatible = module('old')
    Object.assign(incompatible, {
      manifest: { ...incompatible.manifest, capabilities: ['lifecycle'] },
    })
    const fallback = module('compatible')
    const host = new AvatarHostV1([() => incompatible, () => fallback])
    host.mount(document.createElement('div'))

    expect(host.health()).toMatchObject({
      activeModuleId: 'compatible', usingFallback: true, status: 'degraded',
    })
  })
})
