import { describe, expect, it } from 'vitest'
import {
  AVATAR_CONTROL_SCHEMA_VERSION,
  validateAvatarControl,
  type AvatarControlInputV1,
} from '../contract'
import {
  EYE_PUPIL_BOUND_X,
  EYE_PUPIL_BOUND_Y,
  computeEyeRenderState,
  normalizeEyeRenderState,
} from './eyeModel'

function control(overrides: Partial<AvatarControlInputV1> = {}) {
  return validateAvatarControl({
    schemaVersion: AVATAR_CONTROL_SCHEMA_VERSION,
    timestampMs: 1_000,
    idleSeed: 0x51ce_0007,
    lifecycle: 'idle',
    motion: 'full',
    trackingTarget: null,
    speechEnvelope: null,
    ...overrides,
  })
}

function replay(input: ReturnType<typeof control>): string {
  const sequence = [1_000, 1_080, 1_600, 2_400, 3_800, 6_200]
    .map((timeMs) => normalizeEyeRenderState(computeEyeRenderState(input, timeMs)))
  return JSON.stringify(sequence)
}

function replayHash(value: string): string {
  let hash = 0x811c9dc5
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(16).padStart(8, '0')
}

describe('deterministic MVP eye model', () => {
  it('replays the same normalized sequence for the same seed and fixture', () => {
    const fixture = control()
    expect(replay(fixture)).toBe(replay(fixture))
    expect(replay(fixture)).not.toBe(replay(control({ idleSeed: 23 })))
    expect(replayHash(replay(fixture))).toBe('acba732d')
  })

  it('keeps seeded idle and every valid external target inside approved pupil bounds', () => {
    for (let seed = 0; seed < 32; seed += 1) {
      const idle = control({ idleSeed: seed })
      for (let timeMs = 1_000; timeMs < 15_000; timeMs += 137) {
        const state = computeEyeRenderState(idle, timeMs)
        expect(Math.abs(state.pupilX)).toBeLessThanOrEqual(EYE_PUPIL_BOUND_X)
        expect(Math.abs(state.pupilY)).toBeLessThanOrEqual(EYE_PUPIL_BOUND_Y)
        expect(state.blinkClosure).toBeGreaterThanOrEqual(0)
        expect(state.blinkClosure).toBeLessThanOrEqual(1)
      }
    }
    const tracked = computeEyeRenderState(control({
      trackingTarget: { x: 1, y: -1, confidence: 1, observedAtMs: 1_000 },
    }), 1_000)
    expect(tracked.pupilX).toBe(EYE_PUPIL_BOUND_X)
    expect(tracked.pupilY).toBe(-EYE_PUPIL_BOUND_Y)
  })

  it('uses the thinking loader and returns interruption to neutral within 360 ms', () => {
    const thinking = computeEyeRenderState(control({ lifecycle: 'thinking' }), 1_550)
    expect(thinking.lifecycle).toBe('thinking')
    expect(thinking.pupilX).toBe(0)
    expect(thinking.loaderAngleDegrees).toBeGreaterThan(0)

    const interrupted = control({ lifecycle: 'interrupted' })
    expect(computeEyeRenderState(interrupted, 1_359).lifecycle).toBe('interrupted')
    expect(computeEyeRenderState(interrupted, 1_360).lifecycle).toBe('idle')
  })

  it('pulses only speaking state from a validated decoded-playout envelope', () => {
    const speaking = computeEyeRenderState(control({
      lifecycle: 'speaking',
      speechEnvelope: { level: 0.75, observedAtMs: 1_000, source: 'decoded-playout' },
    }), 1_000)
    const thinking = computeEyeRenderState(control({
      lifecycle: 'thinking',
      speechEnvelope: { level: 0.75, observedAtMs: 1_000, source: 'decoded-playout' },
    }), 1_000)

    expect(speaking.speechEnvelope).toBe(0.75)
    expect(speaking.outerScale).toBeGreaterThan(1.15)
    expect(thinking.speechEnvelope).toBe(0)
  })

  it('applies both reduced-motion levels without losing understandable state', () => {
    const ambientReduced = computeEyeRenderState(control({
      lifecycle: 'thinking', motion: 'ambient-reduced',
    }), 2_000)
    const staticFrame = computeEyeRenderState(control({
      lifecycle: 'thinking', motion: 'static',
      trackingTarget: { x: 1, y: 1, confidence: 1, observedAtMs: 1_000 },
      speechEnvelope: { level: 1, observedAtMs: 1_000, source: 'decoded-playout' },
    }), 2_000)

    expect(ambientReduced.lifecycle).toBe('thinking')
    expect(ambientReduced.blinkClosure).toBe(0)
    expect(staticFrame).toMatchObject({
      lifecycle: 'thinking', pupilX: 0, pupilY: 0, blinkClosure: 0,
      loaderAngleDegrees: 0, speechEnvelope: 0,
    })
  })
})
