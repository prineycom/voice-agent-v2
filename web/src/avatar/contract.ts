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
  speechEnvelopeLevel: number
  rejectedSignals: number
}

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
export interface AvatarModuleV1 {
  readonly manifest: AvatarModuleManifestV1
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
const MAX_TARGET_AGE_MS = 750
const MAX_ENVELOPE_AGE_MS = 250
const MAX_FUTURE_SKEW_MS = 100
const MIN_TARGET_CONFIDENCE = 0.2
const MAX_IDLE_SEED = 0xffff_ffff

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function validAge(observedAtMs: number, nowMs: number, maximumAgeMs: number): boolean {
  const age = nowMs - observedAtMs
  return age >= -MAX_FUTURE_SKEW_MS && age <= maximumAgeMs
}

/** Runtime validation is required even though in-repository producers are typed. */
export function validateAvatarControl(input: AvatarControlInputV1): ValidatedAvatarControlV1 {
  let rejectedSignals = 0
  const timestampMs = finite(input.timestampMs) && input.timestampMs >= 0 ? input.timestampMs : 0
  if (timestampMs !== input.timestampMs) rejectedSignals += 1
  const idleSeed = Number.isSafeInteger(input.idleSeed)
    && input.idleSeed >= 0
    && input.idleSeed <= MAX_IDLE_SEED
    ? input.idleSeed
    : 1
  if (idleSeed !== input.idleSeed) rejectedSignals += 1
  const lifecycle = LIFECYCLES.has(input.lifecycle) ? input.lifecycle : 'idle'
  if (lifecycle !== input.lifecycle) rejectedSignals += 1
  const motion = MOTION_PREFERENCES.has(input.motion) ? input.motion : 'static'
  if (motion !== input.motion) rejectedSignals += 1

  let trackingTarget: AvatarTrackingTargetV1 | null = null
  const target = input.trackingTarget
  if (target !== null && target !== undefined) {
    if (
      finite(target.x) && target.x >= -1 && target.x <= 1
      && finite(target.y) && target.y >= -1 && target.y <= 1
      && finite(target.confidence) && target.confidence >= MIN_TARGET_CONFIDENCE && target.confidence <= 1
      && finite(target.observedAtMs) && validAge(target.observedAtMs, timestampMs, MAX_TARGET_AGE_MS)
    ) {
      trackingTarget = { ...target }
    } else {
      rejectedSignals += 1
    }
  }

  let speechEnvelopeLevel = 0
  const envelope = input.speechEnvelope
  if (envelope !== null && envelope !== undefined) {
    if (
      envelope.source === 'decoded-playout'
      && finite(envelope.level) && envelope.level >= 0 && envelope.level <= 1
      && finite(envelope.observedAtMs)
      && validAge(envelope.observedAtMs, timestampMs, MAX_ENVELOPE_AGE_MS)
    ) {
      speechEnvelopeLevel = envelope.level
    } else {
      rejectedSignals += 1
    }
  }

  return {
    schemaVersion: AVATAR_CONTROL_SCHEMA_VERSION,
    timestampMs,
    idleSeed,
    lifecycle,
    motion,
    trackingTarget,
    speechEnvelopeLevel,
    rejectedSignals,
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
