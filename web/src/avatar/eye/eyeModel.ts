import {
  MAX_ENVELOPE_AGE_MS,
  MAX_TARGET_AGE_MS,
  type AvatarLifecycleState,
  type ValidatedAvatarControlV1,
} from '../contract'

export const EYE_PUPIL_BOUND_X = 0.38
export const EYE_PUPIL_BOUND_Y = 0.28
export const INTERRUPTION_RETURN_MS = 360

export interface EyePupilPosition {
  pupilX: number
  pupilY: number
}

export interface EyeRenderState {
  lifecycle: AvatarLifecycleState
  pupilX: number
  pupilY: number
  blinkClosure: number
  outerScale: number
  loaderAngleDegrees: number
  speechEnvelope: number
}

function clamp(value: number, lower: number, upper: number): number {
  return Math.min(upper, Math.max(lower, value))
}

function seededUnit(seed: number, salt: number): number {
  let value = (seed ^ Math.imul(salt, 0x9e3779b1)) >>> 0
  value ^= value << 13
  value ^= value >>> 17
  value ^= value << 5
  return (value >>> 0) / 0xffff_ffff
}

function blinkAt(timeMs: number, seed: number): number {
  const periodMs = 3_400 + Math.round(seededUnit(seed, 3) * 1_400)
  const offsetMs = seededUnit(seed, 5) * periodMs
  const position = (timeMs + offsetMs) % periodMs
  if (position < 105) return position / 105
  if (position < 245) return 1 - ((position - 105) / 140)
  return 0
}

function effectiveLifecycle(
  lifecycle: AvatarLifecycleState,
  elapsedMs: number,
): AvatarLifecycleState {
  return lifecycle === 'interrupted' && elapsedMs >= INTERRUPTION_RETURN_MS ? 'idle' : lifecycle
}

function observationIsFresh(observedAtMs: number, timeMs: number, maximumAgeMs: number): boolean {
  const ageMs = timeMs - observedAtMs
  return ageMs >= 0 && ageMs <= maximumAgeMs
}

/** Pure renderer model: the same validated fixture and time always produce the same state. */
export function computeEyeRenderState(
  control: ValidatedAvatarControlV1,
  timeMs: number,
  interruptionOrigin: EyePupilPosition | null = null,
): EyeRenderState {
  const safeTimeMs = Number.isFinite(timeMs) ? Math.max(control.timestampMs, timeMs) : control.timestampMs
  const elapsedMs = safeTimeMs - control.timestampMs
  const lifecycle = effectiveLifecycle(control.lifecycle, elapsedMs)
  const staticFrame = control.motion === 'static'
  const ambientEnabled = control.motion === 'full'
  let pupilX = 0
  let pupilY = 0

  const trackingTarget = control.trackingTarget !== null
    && observationIsFresh(control.trackingTarget.observedAtMs, safeTimeMs, MAX_TARGET_AGE_MS)
    ? control.trackingTarget
    : null
  if (!staticFrame && lifecycle !== 'thinking') {
    if (trackingTarget !== null && lifecycle !== 'interrupted') {
      const confidence = trackingTarget.confidence
      pupilX = trackingTarget.x * EYE_PUPIL_BOUND_X * confidence
      pupilY = trackingTarget.y * EYE_PUPIL_BOUND_Y * confidence
    } else if (ambientEnabled) {
      const xPhase = seededUnit(control.idleSeed, 7) * Math.PI * 2
      const yPhase = seededUnit(control.idleSeed, 11) * Math.PI * 2
      pupilX = Math.sin((safeTimeMs / 2_900) + xPhase) * EYE_PUPIL_BOUND_X * 0.68
      pupilY = Math.sin((safeTimeMs / 3_700) + yPhase) * EYE_PUPIL_BOUND_Y * 0.62
    }
  }
  if (lifecycle === 'interrupted' && interruptionOrigin !== null && !staticFrame) {
    const progress = clamp(elapsedMs / INTERRUPTION_RETURN_MS, 0, 1)
    const easedProgress = progress * progress * (3 - 2 * progress)
    pupilX = interruptionOrigin.pupilX + (pupilX - interruptionOrigin.pupilX) * easedProgress
    pupilY = interruptionOrigin.pupilY + (pupilY - interruptionOrigin.pupilY) * easedProgress
  }

  const blinkClosure = ambientEnabled && !staticFrame ? blinkAt(safeTimeMs, control.idleSeed) : 0
  const speechEnvelope = lifecycle === 'speaking' && !staticFrame
    && control.speechEnvelope !== null
    && observationIsFresh(control.speechEnvelope.observedAtMs, safeTimeMs, MAX_ENVELOPE_AGE_MS)
    ? clamp(control.speechEnvelope.level, 0, 1)
    : 0
  const stateScale = lifecycle === 'listening'
    ? 1.035
    : lifecycle === 'thinking'
      ? 1.055
      : lifecycle === 'reconnecting'
        ? 1.025
        : 1
  const outerScale = stateScale + speechEnvelope * 0.22
  const loaderAngleDegrees = lifecycle === 'thinking' && !staticFrame
    ? ((elapsedMs / 1_100) * 360) % 360
    : 0

  return {
    lifecycle,
    pupilX: clamp(pupilX, -EYE_PUPIL_BOUND_X, EYE_PUPIL_BOUND_X),
    pupilY: clamp(pupilY, -EYE_PUPIL_BOUND_Y, EYE_PUPIL_BOUND_Y),
    blinkClosure: clamp(blinkClosure, 0, 1),
    outerScale: clamp(outerScale, 1, 1.3),
    loaderAngleDegrees,
    speechEnvelope,
  }
}

export function normalizeEyeRenderState(state: EyeRenderState): EyeRenderState {
  const rounded = (value: number) => Math.round(value * 10_000) / 10_000
  return {
    ...state,
    pupilX: rounded(state.pupilX),
    pupilY: rounded(state.pupilY),
    blinkClosure: rounded(state.blinkClosure),
    outerScale: rounded(state.outerScale),
    loaderAngleDegrees: rounded(state.loaderAngleDegrees),
    speechEnvelope: rounded(state.speechEnvelope),
  }
}
