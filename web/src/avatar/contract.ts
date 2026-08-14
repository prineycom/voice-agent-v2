export const AVATAR_HOST_INTERFACE_VERSION = 'voice-agent.avatar-host.v1' as const
export const AVATAR_CONTROL_SCHEMA_VERSION = 'voice-agent.avatar-control.v1' as const

export const AVATAR_REQUIRED_CAPABILITIES = [
  'lifecycle',
  'speech-envelope',
  'tracking-target',
  'cancellation',
  'reduced-motion',
] as const

export type AvatarCapability = typeof AVATAR_REQUIRED_CAPABILITIES[number]
export type AvatarLifecycleState =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'interrupted'
  | 'error'
  | 'reconnecting'
export type AvatarMotionPreference = 'full' | 'ambient-reduced' | 'static'
export type AvatarHealthStatus = 'ready' | 'degraded' | 'failed'

/** Renderer-neutral target from a future approved producer. Coordinates are normalized. */
export interface AvatarTrackingTargetV1 {
  x: number
  y: number
  confidence: number
  observedAtMs: number
}

/** A bounded observation from the decoded browser playout path, never from generated text. */
export interface AvatarSpeechEnvelopeV1 {
  level: number
  observedAtMs: number
  source: 'decoded-playout'
}

/** The only versioned control input accepted by the avatar host. */
export interface AvatarControlInputV1 {
  schemaVersion: typeof AVATAR_CONTROL_SCHEMA_VERSION
  timestampMs: number
  idleSeed: number
  lifecycle: AvatarLifecycleState
  motion: AvatarMotionPreference
  trackingTarget?: AvatarTrackingTargetV1 | null
  speechEnvelope?: AvatarSpeechEnvelopeV1 | null
}

/** Validated input delivered to a renderer module. Invalid optional signals become safe defaults. */
export interface ValidatedAvatarControlV1 {
  schemaVersion: typeof AVATAR_CONTROL_SCHEMA_VERSION
  timestampMs: number
  idleSeed: number
  lifecycle: AvatarLifecycleState
  motion: AvatarMotionPreference
  trackingTarget: AvatarTrackingTargetV1 | null
  speechEnvelope: AvatarSpeechEnvelopeV1 | null
  rejectedSignals: number
}

export type AvatarControlValidationResultV1 =
  | { accepted: true; control: ValidatedAvatarControlV1 }
  | { accepted: false; rejectedSignals: number }

export interface AvatarModuleManifestV1 {
  interfaceVersion: typeof AVATAR_HOST_INTERFACE_VERSION
  id: string
  displayName: string
  capabilities: readonly AvatarCapability[]
  deterministic: true
}

/**
 * Version 1 renderer boundary. The module owns its DOM/renderer, interpolation,
 * scheduling and frames; it receives only host-validated renderer-neutral input.
 */
export interface AvatarModuleFailureV1 {
  kind: 'render-loop-failed'
}

export type AvatarModuleFailureHandlerV1 = (failure: AvatarModuleFailureV1) => void

export interface AvatarModuleV1 {
  readonly manifest: AvatarModuleManifestV1
  setFailureHandler(handler: AvatarModuleFailureHandlerV1 | null): void
  mount(container: HTMLElement): void
  update(input: ValidatedAvatarControlV1): void
  cancel(timestampMs: number): void
  dispose(): void
}

export type AvatarModuleFactoryV1 = () => AvatarModuleV1

export interface AvatarHealthV1 {
  status: AvatarHealthStatus
  activeModuleId: string | null
  usingFallback: boolean
  rejectedInputs: number
  renderFailures: number
}

const LIFECYCLES = new Set<AvatarLifecycleState>([
  'idle', 'listening', 'thinking', 'speaking', 'interrupted', 'error', 'reconnecting',
])
const MOTION_PREFERENCES = new Set<AvatarMotionPreference>([
  'full', 'ambient-reduced', 'static',
])
export const MAX_TARGET_AGE_MS = 750
export const MAX_ENVELOPE_AGE_MS = 250
export const MAX_FUTURE_SKEW_MS = 100
const MIN_TARGET_CONFIDENCE = 0.2
const MAX_IDLE_SEED = 0xffff_ffff

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function validAge(observedAtMs: number, nowMs: number, maximumAgeMs: number): boolean {
  const age = nowMs - observedAtMs
  return age >= -MAX_FUTURE_SKEW_MS && age <= maximumAgeMs
}

const CONTROL_KEYS = new Set([
  'schemaVersion', 'timestampMs', 'idleSeed', 'lifecycle', 'motion',
  'trackingTarget', 'speechEnvelope',
])
const TARGET_KEYS = new Set(['x', 'y', 'confidence', 'observedAtMs'])
const ENVELOPE_KEYS = new Set(['level', 'observedAtMs', 'source'])

function recordHasOnlyKeys(value: unknown, keys: ReadonlySet<string>): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    && Object.keys(value).every((key) => keys.has(key))
}

function invalidControl(): AvatarControlValidationResultV1 {
  return { accepted: false, rejectedSignals: 1 }
}

export function avatarTimestampIsAdmissible(timestampMs: unknown, hostNowMs: number): timestampMs is number {
  return finite(timestampMs)
    && timestampMs >= 0
    && finite(hostNowMs)
    && hostNowMs >= 0
    && timestampMs <= hostNowMs + MAX_FUTURE_SKEW_MS
}

/** Runtime validation is required even though in-repository producers are typed. */
export function validateAvatarControl(
  input: unknown,
  hostNowMs: number = performance.now(),
): AvatarControlValidationResultV1 {
  try {
    if (typeof input !== 'object' || input === null || Array.isArray(input)) return invalidControl()
    const value = input as Record<string, unknown>
    const keys = Object.keys(value)
    if (keys.some((key) => !CONTROL_KEYS.has(key))) return invalidControl()
    if (
      value.schemaVersion !== AVATAR_CONTROL_SCHEMA_VERSION
      || !avatarTimestampIsAdmissible(value.timestampMs, hostNowMs)
      || !Number.isSafeInteger(value.idleSeed) || (value.idleSeed as number) < 0
      || (value.idleSeed as number) > MAX_IDLE_SEED
      || !LIFECYCLES.has(value.lifecycle as AvatarLifecycleState)
      || !MOTION_PREFERENCES.has(value.motion as AvatarMotionPreference)
    ) return invalidControl()

    const timestampMs = value.timestampMs
    let rejectedSignals = 0
    let trackingTarget: AvatarTrackingTargetV1 | null = null
    const target = value.trackingTarget
    if (target !== null && target !== undefined) {
      if (
        recordHasOnlyKeys(target, TARGET_KEYS)
        && finite(target.x) && target.x >= -1 && target.x <= 1
        && finite(target.y) && target.y >= -1 && target.y <= 1
        && finite(target.confidence) && target.confidence >= MIN_TARGET_CONFIDENCE
        && target.confidence <= 1
        && finite(target.observedAtMs)
        && validAge(target.observedAtMs, timestampMs, MAX_TARGET_AGE_MS)
      ) {
        trackingTarget = {
          x: target.x,
          y: target.y,
          confidence: target.confidence,
          observedAtMs: target.observedAtMs,
        }
      } else {
        rejectedSignals += 1
      }
    }

    let speechEnvelope: AvatarSpeechEnvelopeV1 | null = null
    const envelope = value.speechEnvelope
    if (envelope !== null && envelope !== undefined) {
      if (
        recordHasOnlyKeys(envelope, ENVELOPE_KEYS)
        && envelope.source === 'decoded-playout'
        && finite(envelope.level) && envelope.level >= 0 && envelope.level <= 1
        && finite(envelope.observedAtMs)
        && validAge(envelope.observedAtMs, timestampMs, MAX_ENVELOPE_AGE_MS)
      ) {
        speechEnvelope = {
          level: envelope.level,
          observedAtMs: envelope.observedAtMs,
          source: envelope.source,
        }
      } else {
        rejectedSignals += 1
      }
    }

    return {
      accepted: true,
      control: {
        schemaVersion: AVATAR_CONTROL_SCHEMA_VERSION,
        timestampMs,
        idleSeed: value.idleSeed as number,
        lifecycle: value.lifecycle as AvatarLifecycleState,
        motion: value.motion as AvatarMotionPreference,
        trackingTarget,
        speechEnvelope,
        rejectedSignals,
      },
    }
  } catch {
    return invalidControl()
  }
}

export function manifestIsCompatible(manifest: AvatarModuleManifestV1): boolean {
  if (
    manifest.interfaceVersion !== AVATAR_HOST_INTERFACE_VERSION
    || !manifest.id
    || manifest.id.length > 96
    || manifest.deterministic !== true
  ) return false
  const capabilities = new Set(manifest.capabilities)
  return AVATAR_REQUIRED_CAPABILITIES.every((capability) => capabilities.has(capability))
}
