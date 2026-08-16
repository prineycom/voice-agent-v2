import type { SpeechEnvelopeStatus } from './playback'

export const CONTROL_VERSION = 'voice-agent.realtime-control.v2'
export const CLIENT_CONTROL_VERSION = 'voice-agent.client-control.v1'
export const CONTROL_TOPIC = 'voice-agent.control.v2'
export const CLIENT_CONTROL_TOPIC = 'voice-agent.client-control.v1'
export const ACTIVE_LLM_MODEL_IDENTITY = 'LiquidAI/LFM2.5-2.6B-GGUF@b421ad1d549afeda6a0fb2ad3a697cb5a7879adc#Q4_K_M'

const MAX_CONTROL_BYTES = 65_536
const MAX_SEQUENCE = 1_000_000_000
const CORRELATION_ID = /^[a-z0-9][a-z0-9-]{0,63}$/

export type ConnectionState = 'idle' | 'connecting' | 'ready' | 'reconnecting' | 'closed' | 'failed'
export type UserVisibleState = 'available' | 'unavailable' | 'degraded' | 'retrying' | 'interrupted'
export type MicrophoneLifecycle = 'disconnected' | 'requesting-permission' | 'publishing' | 'live' | 'muted' | 'error'
export type TurnPhase = 'idle' | 'listening' | 'thinking' | 'speaking'
export type TurnOutcome = 'completed' | 'interrupted' | 'failed'
export type HealthComponentName = 'livekit' | 'controller' | 'stt' | 'selected_llm' | 'tts'
export type LivenessState = 'alive' | 'dead' | 'unknown'
export type ReadinessState = 'ready' | 'unready' | 'degraded' | 'unknown'

export interface ComponentHealthObservation {
  component: HealthComponentName
  liveness: LivenessState
  readiness: ReadinessState
  compatible: boolean
  identity: string
  contract_version: string
  reason_code: string | null
  retry_count: number
  retry_limit: number
}

export interface HealthReadinessReport {
  schema_version: 'voice-agent.health-readiness.v1'
  overall_readiness: 'ready' | 'unready'
  components: ComponentHealthObservation[]
  provider_mode: 'local'
  external_transfer: false
  automatic_fallback: false
  stt_location: 'local'
  tts_location: 'local'
  auth_boundary: 'tailnet'
  wake_enabled: false
  selected_avatar_module: 'mvp-eye-svg-v1'
}
export type ControlEventType =
  | 'session.ready'
  | 'session.reconnected'
  | 'session.degraded'
  | 'turn.listening'
  | 'turn.media-ready'
  | 'stt.final'
  | 'turn.thinking'
  | 'llm.visible'
  | 'turn.speaking'
  | 'turn.completed'
  | 'turn.interrupted'
  | 'turn.failed'

const EVENT_TYPES = new Set<ControlEventType>([
  'session.ready', 'session.reconnected', 'session.degraded',
  'turn.listening', 'turn.media-ready', 'stt.final', 'turn.thinking', 'llm.visible',
  'turn.speaking', 'turn.completed', 'turn.interrupted', 'turn.failed',
])
const TERMINAL_TYPES = new Set<ControlEventType>([
  'turn.completed', 'turn.interrupted', 'turn.failed',
])
const TURN_PREDECESSOR = new Map<ControlEventType, ControlEventType | ControlEventType[]>([
  ['turn.media-ready', 'turn.listening'],
  ['stt.final', 'turn.media-ready'],
  ['turn.thinking', 'stt.final'],
  ['llm.visible', ['turn.thinking', 'llm.visible', 'turn.speaking']],
  ['turn.speaking', ['turn.thinking', 'llm.visible']],
  ['turn.completed', ['turn.thinking', 'llm.visible', 'turn.speaking']],
])

export interface ControlEvent {
  schema_version: typeof CONTROL_VERSION
  session_id: string
  turn_id: string
  stream_epoch: number
  turn_generation: number
  request_id: string
  media_generation: number
  sequence: number
  type: ControlEventType
  terminal: boolean
  payload: Record<string, unknown>
}

export interface TTSProfile {
  profile: 'silero-kseniya'
  backend: 'silero'
  speaker: 'kseniya'
  output_sample_rate_hz: 48000
  native_sample_rate_hz: 48000
  license: 'CC-BY-NC-SA-4.0'
  private_noncommercial_only: boolean
}

export interface LLMProfile {
  provider_mode: 'local'
  model_identity: typeof ACTIVE_LLM_MODEL_IDENTITY
}

export interface SessionCapability {
  session_id: string
  stream_epoch: number
  livekit_url: string
  token: string
  expires_in_seconds: number
  admission_timeout_ms: number
  control_version: typeof CONTROL_VERSION
  llm_profile: LLMProfile
  tts_profile: TTSProfile
}

export interface TurnHistoryItem {
  turnId: string
  user: string
  assistant: string
  outcome: TurnOutcome | null
  audioUnavailable: boolean
  endpointToFirstVisibleMs: number | null
  endpointToFirstAcceptedPcmMs: number | null
  endpointToSttFinalMs?: number | null
  providerTimeToFirstTokenMs?: number | null
  providerCompletionMs?: number | null
  ttsTimeToFirstAudioMs?: number | null
  cancellationLatencyMs?: number | null
  totalTurnMs?: number | null
  slowestStage?: string | null
  providerMode?: string | null
  providerIdentity?: string | null
  externalTransfer?: boolean | null
  providerInputUnitCount?: number | null
  providerOutputUnitCount?: number | null
  providerTotalUnitCount?: number | null
  pcmQueueMaxBlocks?: number | null
  segmentQueueMaxSegments?: number | null
  cancellationCount?: number
  staleDropCount?: number
  cpuUtilizationPercent?: number | null
  hostRamUsedMib?: number | null
  processRssMib?: number | null
  gpuVramUsedMib?: number | null
  gpuUtilizationPercent?: number | null
  userState?: UserVisibleState
  failureStage?: string | null
  failureCode?: string | null
}

export interface VoiceState {
  connection: ConnectionState
  availability: UserVisibleState
  sessionId: string | null
  streamEpoch: number
  lastSequence: number
  currentTurnId: string | null
  currentTurnGeneration: number
  currentRequestId: string | null
  currentMediaGeneration: number
  currentTurnTerminal: boolean
  lastTurnEvent: ControlEventType | null
  phase: TurnPhase
  transcript: string
  response: string
  history: TurnHistoryItem[]
  error: string | null
  failureStage: string | null
  failureCode: string | null
  retryCount: number
  retryLimit: number
  health: HealthReadinessReport | null
  droppedEvents: number
  lateControlDegraded: boolean
  audioBlocked: boolean
  speechEnvelopeStatus: SpeechEnvelopeStatus
  microphoneStatus: MicrophoneLifecycle
  microphoneAvailable: boolean
  microphoneEnabled: boolean
  microphoneTransitioning: boolean
  microphoneError: string | null
  llmProfile: LLMProfile | null
  ttsProfile: TTSProfile | null
}

export const initialVoiceState: VoiceState = {
  connection: 'idle',
  availability: 'unavailable',
  sessionId: null,
  streamEpoch: 0,
  lastSequence: 0,
  currentTurnId: null,
  currentTurnGeneration: 0,
  currentRequestId: null,
  currentMediaGeneration: 0,
  currentTurnTerminal: true,
  lastTurnEvent: null,
  phase: 'idle',
  transcript: '',
  response: '',
  history: [],
  error: null,
  failureStage: null,
  failureCode: null,
  retryCount: 0,
  retryLimit: 0,
  health: null,
  droppedEvents: 0,
  lateControlDegraded: false,
  audioBlocked: false,
  speechEnvelopeStatus: 'unknown',
  microphoneStatus: 'disconnected',
  microphoneAvailable: false,
  microphoneEnabled: false,
  microphoneTransitioning: false,
  microphoneError: null,
  llmProfile: null,
  ttsProfile: null,
}

export type VoiceAction =
  | { type: 'session-created'; capability: SessionCapability }
  | { type: 'connection'; connection: ConnectionState; error?: string }
  | { type: 'control'; event: ControlEvent }
  | { type: 'drop' }
  | { type: 'audio-blocked'; blocked: boolean }
  | { type: 'speech-envelope-status'; status: SpeechEnvelopeStatus }
  | { type: 'microphone-lifecycle'; status: MicrophoneLifecycle }
  | { type: 'microphone'; enabled: boolean; transitioning: boolean; error?: string }
  | { type: 'reset' }

function ownObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype
}

function boundedValue(value: unknown, depth = 0): boolean {
  if (depth > 5) return false
  if (value === null || typeof value === 'boolean') return true
  if (typeof value === 'number') return Number.isFinite(value)
  if (typeof value === 'string') return value.length <= 8192
  if (Array.isArray(value)) return value.length <= 128 && value.every((item) => boundedValue(item, depth + 1))
  if (ownObject(value)) {
    const entries = Object.entries(value)
    return entries.length <= 64 && entries.every(([key, item]) => key.length <= 128 && boundedValue(item, depth + 1))
  }
  return false
}

export function parseHealthReadinessReport(value: unknown): HealthReadinessReport | null {
  if (!ownObject(value)) return null
  const expected = [
    'auth_boundary', 'automatic_fallback', 'components', 'external_transfer',
    'overall_readiness', 'provider_mode', 'schema_version', 'selected_avatar_module',
    'stt_location', 'tts_location', 'wake_enabled',
  ]
  const keys = Object.keys(value).sort()
  if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) return null
  if (
    value.schema_version !== 'voice-agent.health-readiness.v1'
    || !['ready', 'unready'].includes(String(value.overall_readiness))
    || value.provider_mode !== 'local'
    || value.external_transfer !== false
    || value.automatic_fallback !== false
    || value.stt_location !== 'local'
    || value.tts_location !== 'local'
    || value.auth_boundary !== 'tailnet'
    || value.wake_enabled !== false
    || value.selected_avatar_module !== 'mvp-eye-svg-v1'
    || !Array.isArray(value.components)
    || value.components.length !== 5
  ) return null
  const componentNames: HealthComponentName[] = ['livekit', 'controller', 'stt', 'selected_llm', 'tts']
  const components: ComponentHealthObservation[] = []
  for (const candidate of value.components) {
    if (!ownObject(candidate)) return null
    const componentKeys = Object.keys(candidate).sort()
    const expectedComponentKeys = [
      'compatible', 'component', 'contract_version', 'identity', 'liveness',
      'readiness', 'reason_code', 'retry_count', 'retry_limit',
    ]
    if (
      componentKeys.length !== expectedComponentKeys.length
      || componentKeys.some((key, index) => key !== expectedComponentKeys[index])
      || !componentNames.includes(candidate.component as HealthComponentName)
      || !['alive', 'dead', 'unknown'].includes(String(candidate.liveness))
      || !['ready', 'unready', 'degraded', 'unknown'].includes(String(candidate.readiness))
      || typeof candidate.compatible !== 'boolean'
      || typeof candidate.identity !== 'string' || !candidate.identity || candidate.identity.length > 256
      || typeof candidate.contract_version !== 'string' || !candidate.contract_version || candidate.contract_version.length > 128
      || !(candidate.reason_code === null || typeof candidate.reason_code === 'string' && /^[a-z0-9_]{1,64}$/.test(candidate.reason_code))
      || !Number.isSafeInteger(candidate.retry_count) || (candidate.retry_count as number) < 0
      || !Number.isSafeInteger(candidate.retry_limit) || (candidate.retry_limit as number) < (candidate.retry_count as number)
      || (candidate.retry_limit as number) > 16
    ) return null
    components.push(candidate as unknown as ComponentHealthObservation)
  }
  if (new Set(components.map((component) => component.component)).size !== componentNames.length) return null
  const hardReady = components.every((component) => (
    component.liveness === 'alive' && component.readiness === 'ready' && component.compatible
  ))
  if ((value.overall_readiness === 'ready') !== hardReady) return null
  return { ...value, components } as unknown as HealthReadinessReport
}

export function parseControlEvent(payload: Uint8Array | string): ControlEvent | null {
  let text: string
  try {
    text = typeof payload === 'string' ? payload : new TextDecoder('utf-8', { fatal: true }).decode(payload)
  } catch {
    return null
  }
  if (!text || new TextEncoder().encode(text).byteLength > MAX_CONTROL_BYTES) return null
  let value: unknown
  try {
    value = JSON.parse(text)
  } catch {
    return null
  }
  if (!ownObject(value)) return null
  const keys = Object.keys(value).sort()
  const expected = ['media_generation', 'payload', 'request_id', 'schema_version', 'sequence', 'session_id', 'stream_epoch', 'terminal', 'turn_generation', 'turn_id', 'type']
  if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) return null
  const eventType = value.type
  if (
    value.schema_version !== CONTROL_VERSION
    || typeof value.session_id !== 'string' || !CORRELATION_ID.test(value.session_id)
    || typeof value.turn_id !== 'string' || !CORRELATION_ID.test(value.turn_id)
    || !Number.isSafeInteger(value.stream_epoch) || (value.stream_epoch as number) < 1 || (value.stream_epoch as number) > MAX_SEQUENCE
    || !Number.isSafeInteger(value.turn_generation) || (value.turn_generation as number) < 0 || (value.turn_generation as number) > MAX_SEQUENCE
    || typeof value.request_id !== 'string' || !CORRELATION_ID.test(value.request_id)
    || !Number.isSafeInteger(value.media_generation) || (value.media_generation as number) < 0 || (value.media_generation as number) > MAX_SEQUENCE
    || !Number.isSafeInteger(value.sequence) || (value.sequence as number) < 1 || (value.sequence as number) > MAX_SEQUENCE
    || typeof eventType !== 'string' || !EVENT_TYPES.has(eventType as ControlEventType)
    || typeof value.terminal !== 'boolean' || value.terminal !== TERMINAL_TYPES.has(eventType as ControlEventType)
    || !ownObject(value.payload) || !boundedValue(value.payload)
  ) return null
  if (eventType === 'session.ready' || eventType === 'session.degraded') {
    const health = parseHealthReadinessReport(value.payload.health)
    if (
      health === null
      || eventType === 'session.ready' && health.overall_readiness !== 'ready'
      || eventType === 'session.degraded' && health.overall_readiness !== 'unready'
    ) return null
  }
  return value as unknown as ControlEvent
}

export class RealtimeControlGate {
  private streamEpoch: number
  private lastSequence = 0
  private currentTurnId: string | null = null
  private currentTurnTerminal = true
  private currentTurnGeneration = 0
  private currentRequestId = 'session'
  private currentMediaGeneration = 0
  private lastTurnEvent: ControlEventType | null = null
  private reconnecting = false

  constructor(private readonly sessionId: string, streamEpoch: number) {
    this.streamEpoch = streamEpoch
  }

  beginReconnect(): void {
    this.reconnecting = true
    this.currentTurnId = null
    this.currentTurnTerminal = true
    this.currentTurnGeneration = 0
    this.currentRequestId = 'session'
    this.currentMediaGeneration = 0
    this.lastTurnEvent = null
  }

  accept(event: ControlEvent): boolean {
    if (event.session_id !== this.sessionId || event.sequence <= this.lastSequence) return false
    if (this.reconnecting && (event.type === 'session.reconnected' || event.type === 'session.degraded')) {
      if (
        event.turn_id !== 'session'
        || event.stream_epoch !== this.streamEpoch + 1
        || event.turn_generation !== 0
        || event.request_id !== 'session'
        || event.media_generation !== 0
      ) return false
      this.streamEpoch = event.stream_epoch
      this.lastSequence = event.sequence
      this.reconnecting = false
      return true
    }
    if (this.reconnecting || event.stream_epoch !== this.streamEpoch) return false
    if (event.type.startsWith('session.')) {
      if (
        event.turn_id !== 'session'
        || event.turn_generation !== 0
        || event.request_id !== 'session'
        || event.media_generation !== 0
      ) return false
      this.lastSequence = event.sequence
      return true
    }
    if (event.type === 'turn.listening' || initialPublicationFailure(event)) {
      if (
        !this.currentTurnTerminal
        || this.currentTurnId === event.turn_id
        || event.turn_generation < 1
        || event.request_id === 'session'
        || event.media_generation < 1
      ) return false
      this.currentTurnId = event.turn_id
      this.currentTurnGeneration = event.turn_generation
      this.currentRequestId = event.request_id
      this.currentMediaGeneration = event.media_generation
      this.currentTurnTerminal = event.terminal
      this.lastTurnEvent = event.type
    } else {
      if (
        event.turn_id !== this.currentTurnId
        || event.turn_generation !== this.currentTurnGeneration
        || event.request_id !== this.currentRequestId
        || event.media_generation !== this.currentMediaGeneration
        || this.currentTurnTerminal
        || !validTurnTransition(this.lastTurnEvent, event.type)
      ) return false
      this.lastTurnEvent = event.type
      if (event.terminal) this.currentTurnTerminal = true
    }
    this.lastSequence = event.sequence
    return true
  }
}

function validLiveKitUrl(value: unknown): value is string {
  if (typeof value !== 'string' || value.length > 2048) return false
  try {
    const url = new URL(value)
    return url.protocol === 'wss:' || (
      url.protocol === 'ws:'
      && (url.hostname === '127.0.0.1' || url.hostname === 'localhost' || url.hostname === '::1')
    )
  } catch {
    return false
  }
}

function parseTTSProfile(value: unknown): TTSProfile | null {
  if (!ownObject(value)) return null
  const keys = Object.keys(value).sort()
  const expected = ['backend', 'license', 'native_sample_rate_hz', 'output_sample_rate_hz', 'private_noncommercial_only', 'profile', 'speaker']
  if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) return null
  const silero = (
    value.profile === 'silero-kseniya'
    && value.backend === 'silero'
    && value.speaker === 'kseniya'
    && value.native_sample_rate_hz === 48_000
    && value.license === 'CC-BY-NC-SA-4.0'
    && value.private_noncommercial_only === true
  )
  if (value.output_sample_rate_hz !== 48_000 || !silero) return null
  return value as unknown as TTSProfile
}

function parseLLMProfile(value: unknown): LLMProfile | null {
  if (!ownObject(value)) return null
  const keys = Object.keys(value).sort()
  const expected = ['model_identity', 'provider_mode']
  if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) return null
  if (value.provider_mode !== 'local' || value.model_identity !== ACTIVE_LLM_MODEL_IDENTITY) return null
  return value as unknown as LLMProfile
}

export function parseCapability(value: unknown): SessionCapability | null {
  if (!ownObject(value)) return null
  const keys = Object.keys(value).sort()
  const expected = ['admission_timeout_ms', 'control_version', 'expires_in_seconds', 'livekit_url', 'llm_profile', 'session_id', 'stream_epoch', 'token', 'tts_profile']
  if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) return null
  if (
    typeof value.session_id !== 'string' || !CORRELATION_ID.test(value.session_id)
    || value.stream_epoch !== 1
    || !validLiveKitUrl(value.livekit_url)
    || typeof value.token !== 'string' || value.token.length < 16 || value.token.length > 8192
    || !Number.isSafeInteger(value.expires_in_seconds) || (value.expires_in_seconds as number) < 1 || (value.expires_in_seconds as number) > 600
    || !Number.isSafeInteger(value.admission_timeout_ms) || (value.admission_timeout_ms as number) < 1_000 || (value.admission_timeout_ms as number) > 60_000
    || (value.admission_timeout_ms as number) > (value.expires_in_seconds as number) * 1_000
    || value.control_version !== CONTROL_VERSION
    || parseLLMProfile(value.llm_profile) === null
    || parseTTSProfile(value.tts_profile) === null
  ) return null
  return value as unknown as SessionCapability
}

function drop(state: VoiceState): VoiceState {
  const activeSession = state.sessionId !== null
  const readySession = state.connection === 'ready'
  return {
    ...state,
    availability: readySession ? 'degraded' : state.availability,
    failureStage: activeSession ? 'controller' : state.failureStage,
    failureCode: activeSession ? 'late_or_duplicate_event' : state.failureCode,
    lateControlDegraded: activeSession || state.lateControlDegraded,
    droppedEvents: state.droppedEvents + 1,
  }
}

function hasLateControlDegradation(state: VoiceState): boolean {
  return state.lateControlDegraded
}

function initialPublicationFailure(event: ControlEvent): boolean {
  return (
    event.type === 'turn.failed'
    && event.terminal
    && event.payload.outcome === 'failed'
    && event.payload.stage === 'publication'
    && typeof event.payload.code === 'string'
    && /^[a-z0-9_]{1,64}$/.test(event.payload.code)
    && event.payload.dependency_class === 'hard'
    && event.payload.failure_matrix_id === 'livekit_unavailable'
    && event.payload.admit_turn === false
    && event.payload.user_state === 'retrying'
  )
}

function validTurnTransition(previous: ControlEventType | null, next: ControlEventType): boolean {
  if (next === 'turn.interrupted' || next === 'turn.failed') return previous !== null
  const predecessors = TURN_PREDECESSOR.get(next)
  return Array.isArray(predecessors)
    ? predecessors.includes(previous as ControlEventType)
    : predecessors === previous
}

function metric(payload: Record<string, unknown>, name: string): number | null {
  const value = payload[name]
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
}

function count(payload: Record<string, unknown>, name: string): number | null {
  const value = payload[name]
  return Number.isSafeInteger(value) && (value as number) >= 0 ? value as number : null
}

function failureCode(payload: Record<string, unknown>, name: 'stage' | 'code', fallback: string): string {
  const value = payload[name]
  return typeof value === 'string' && /^[a-z0-9_]{1,64}$/.test(value) ? value : fallback
}

function visibleState(payload: Record<string, unknown>, fallback: UserVisibleState): UserVisibleState {
  const value = payload.user_state
  return ['available', 'unavailable', 'degraded', 'retrying', 'interrupted'].includes(String(value))
    ? value as UserVisibleState
    : fallback
}

function updateHistory(
  history: TurnHistoryItem[],
  turnId: string,
  update: (item: TurnHistoryItem) => TurnHistoryItem,
): TurnHistoryItem[] {
  const index = history.findIndex((item) => item.turnId === turnId)
  if (index < 0) return history
  const next = history.slice()
  next[index] = update(next[index])
  return next
}

function interruptCurrentHistory(state: VoiceState): TurnHistoryItem[] {
  if (state.currentTurnId === null || state.currentTurnTerminal) return state.history
  return updateHistory(state.history, state.currentTurnId, (item) => ({
    ...item,
    outcome: 'interrupted',
  }))
}

export function voiceReducer(state: VoiceState, action: VoiceAction): VoiceState {
  if (action.type === 'reset') return initialVoiceState
  if (action.type === 'drop') return drop(state)
  if (action.type === 'audio-blocked') return { ...state, audioBlocked: action.blocked }
  if (action.type === 'speech-envelope-status') {
    return { ...state, speechEnvelopeStatus: action.status }
  }
  if (action.type === 'microphone-lifecycle') {
    return {
      ...state,
      microphoneStatus: action.status,
      microphoneAvailable: action.status === 'error'
        ? state.microphoneAvailable
        : action.status === 'live' || action.status === 'muted',
    }
  }
  if (action.type === 'microphone') {
    return {
      ...state,
      microphoneEnabled: action.enabled,
      microphoneTransitioning: action.transitioning,
      microphoneError: action.error ?? null,
    }
  }
  if (action.type === 'session-created') {
    return {
      ...initialVoiceState,
      connection: 'connecting',
      availability: 'unavailable',
      sessionId: action.capability.session_id,
      streamEpoch: action.capability.stream_epoch,
      llmProfile: action.capability.llm_profile,
      ttsProfile: action.capability.tts_profile,
    }
  }
  if (action.type === 'connection') {
    if (action.connection === 'reconnecting') {
      return {
        ...state,
        connection: 'reconnecting',
        availability: hasLateControlDegradation(state) ? 'degraded' : 'retrying',
        retryCount: Math.min(state.retryCount + 1, 10),
        retryLimit: 10,
        currentTurnTerminal: true,
        currentTurnGeneration: 0,
        currentRequestId: null,
        currentMediaGeneration: 0,
        lastTurnEvent: null,
        phase: 'idle',
        history: interruptCurrentHistory(state),
        error: null,
      }
    }
    return {
      ...state,
      connection: action.connection,
      availability: action.connection === 'failed'
        ? state.availability === 'degraded' || state.availability === 'interrupted'
          ? state.availability
          : 'unavailable'
        : action.connection === 'ready'
          ? hasLateControlDegradation(state) ? 'degraded' : 'available'
          : state.availability,
      error: action.error ?? (action.connection === 'failed' ? 'Соединение недоступно' : null),
    }
  }

  const event = action.event
  if (state.sessionId === null || event.session_id !== state.sessionId || event.sequence <= state.lastSequence) return drop(state)
  if (
    state.connection === 'reconnecting'
    && (event.type === 'session.reconnected' || event.type === 'session.degraded')
  ) {
    if (
      event.turn_id !== 'session'
      || event.stream_epoch !== state.streamEpoch + 1
      || event.turn_generation !== 0
      || event.request_id !== 'session'
      || event.media_generation !== 0
    ) return drop(state)
    const degraded = event.type === 'session.degraded'
    const preserveLateDegradation = !degraded && hasLateControlDegradation(state)
    const health = degraded ? parseHealthReadinessReport(event.payload.health) : state.health
    if (degraded && health === null) return drop(state)
    return {
      ...state,
      connection: degraded ? 'failed' : 'reconnecting',
      availability: degraded
        ? visibleState(event.payload, 'unavailable')
        : hasLateControlDegradation(state) ? 'degraded' : 'retrying',
      streamEpoch: event.stream_epoch,
      lastSequence: event.sequence,
      currentTurnId: null,
      currentTurnGeneration: 0,
      currentRequestId: null,
      currentMediaGeneration: 0,
      currentTurnTerminal: true,
      lastTurnEvent: null,
      phase: 'idle',
      error: degraded ? 'Не удалось безопасно восстановить сессию' : null,
      failureStage: degraded
        ? failureCode(event.payload, 'stage', 'session')
        : preserveLateDegradation ? state.failureStage : null,
      failureCode: degraded
        ? failureCode(event.payload, 'code', 'degraded')
        : preserveLateDegradation ? state.failureCode : null,
      retryCount: degraded ? count(event.payload, 'retry_count') ?? 0 : state.retryCount,
      retryLimit: degraded ? count(event.payload, 'retry_limit') ?? 0 : state.retryLimit,
      health,
    }
  }
  if (state.connection === 'reconnecting' && event.type === 'session.ready') {
    if (
      event.turn_id !== 'session'
      || event.stream_epoch !== state.streamEpoch
      || event.turn_generation !== 0
      || event.request_id !== 'session'
      || event.media_generation !== 0
    ) return drop(state)
    const health = parseHealthReadinessReport(event.payload.health)
    if (health === null || health.overall_readiness !== 'ready') return drop(state)
    const preserveLateDegradation = hasLateControlDegradation(state)
    return {
      ...state,
      connection: 'ready',
      availability: preserveLateDegradation ? 'degraded' : 'available',
      lastSequence: event.sequence,
      error: null,
      failureStage: preserveLateDegradation ? state.failureStage : null,
      failureCode: preserveLateDegradation ? state.failureCode : null,
      retryCount: preserveLateDegradation ? state.retryCount : 0,
      retryLimit: preserveLateDegradation ? state.retryLimit : 0,
      health,
    }
  }
  if (state.connection === 'reconnecting' || event.stream_epoch !== state.streamEpoch) return drop(state)
  if (event.type.startsWith('session.')) {
    if (
      event.turn_id !== 'session'
      || event.turn_generation !== 0
      || event.request_id !== 'session'
      || event.media_generation !== 0
    ) return drop(state)
    if (event.type === 'session.reconnected') {
      return {
        ...state,
        connection: 'reconnecting',
        availability: hasLateControlDegradation(state) ? 'degraded' : 'retrying',
        lastSequence: event.sequence,
      }
    }
    const health = parseHealthReadinessReport(event.payload.health)
    if (health === null) return drop(state)
    const degraded = event.type === 'session.degraded'
    if (!degraded && health.overall_readiness !== 'ready') return drop(state)
    const preserveLateDegradation = !degraded && hasLateControlDegradation(state)
    return {
      ...state,
      connection: degraded ? 'failed' : 'ready',
      availability: degraded
        ? visibleState(event.payload, 'unavailable')
        : preserveLateDegradation ? 'degraded' : 'available',
      lastSequence: event.sequence,
      error: degraded ? 'Локальный голосовой путь недоступен' : null,
      failureStage: degraded
        ? failureCode(event.payload, 'stage', 'session')
        : preserveLateDegradation ? state.failureStage : null,
      failureCode: degraded
        ? failureCode(event.payload, 'code', 'degraded')
        : preserveLateDegradation ? state.failureCode : null,
      retryCount: degraded
        ? count(event.payload, 'retry_count') ?? 0
        : preserveLateDegradation ? state.retryCount : 0,
      retryLimit: degraded
        ? count(event.payload, 'retry_limit') ?? 0
        : preserveLateDegradation ? state.retryLimit : 0,
      health,
    }
  }

  let next = state
  if (event.type === 'turn.listening' || initialPublicationFailure(event)) {
    if (
      !state.currentTurnTerminal
      || state.currentTurnId === event.turn_id
      || event.turn_generation < 1
      || event.request_id === 'session'
      || event.media_generation < 1
    ) return drop(state)
    const item: TurnHistoryItem = {
      turnId: event.turn_id,
      user: '',
      assistant: '',
      outcome: null,
      audioUnavailable: false,
      endpointToFirstVisibleMs: null,
      endpointToFirstAcceptedPcmMs: null,
    }
    const preserveLateDegradation = hasLateControlDegradation(state)
    next = {
      ...state,
      currentTurnId: event.turn_id,
      currentTurnGeneration: event.turn_generation,
      currentRequestId: event.request_id,
      currentMediaGeneration: event.media_generation,
      currentTurnTerminal: false,
      lastTurnEvent: 'turn.listening',
      phase: 'listening',
      availability: preserveLateDegradation ? 'degraded' : 'available',
      transcript: '',
      response: '',
      history: [...state.history, item],
      error: null,
      failureStage: preserveLateDegradation ? state.failureStage : null,
      failureCode: preserveLateDegradation ? state.failureCode : null,
      retryCount: preserveLateDegradation ? state.retryCount : 0,
      retryLimit: preserveLateDegradation ? state.retryLimit : 0,
      lastSequence: event.type === 'turn.listening' ? event.sequence : state.lastSequence,
    }
    if (event.type === 'turn.listening') return next
  }
  if (
    event.turn_id !== next.currentTurnId
    || event.turn_generation !== next.currentTurnGeneration
    || event.request_id !== next.currentRequestId
    || event.media_generation !== next.currentMediaGeneration
    || next.currentTurnTerminal
    || !validTurnTransition(next.lastTurnEvent, event.type)
  ) return drop(state)

  let phase: TurnPhase = next.phase
  if (event.type === 'stt.final' || event.type === 'turn.thinking') phase = 'thinking'
  if (event.type === 'llm.visible' && next.phase !== 'speaking') phase = 'thinking'
  if (event.type === 'turn.speaking') phase = 'speaking'
  if (event.terminal) phase = 'idle'
  const transcript = event.type === 'stt.final' && typeof event.payload.transcript === 'string'
    ? event.payload.transcript : next.transcript
  const response = event.type === 'llm.visible' && typeof event.payload.response === 'string'
    ? event.payload.response : next.response
  const outcome: TurnOutcome | null = event.type === 'turn.completed'
    ? 'completed'
    : event.type === 'turn.interrupted'
      ? 'interrupted'
      : event.type === 'turn.failed'
        ? 'failed'
        : null
  const visibleMetric = metric(event.payload, 'endpoint_to_first_visible_ms')
  const pcmMetric = metric(event.payload, 'endpoint_to_first_accepted_pcm_ms')
  const history = updateHistory(next.history, event.turn_id, (item) => ({
    ...item,
    user: transcript,
    assistant: response,
    outcome: outcome ?? item.outcome,
    audioUnavailable: item.audioUnavailable || (
      event.type === 'turn.failed' && event.payload.stage === 'tts'
    ),
    endpointToFirstVisibleMs: visibleMetric ?? item.endpointToFirstVisibleMs,
    endpointToFirstAcceptedPcmMs: pcmMetric ?? item.endpointToFirstAcceptedPcmMs,
    endpointToSttFinalMs: metric(event.payload, 'endpoint_to_stt_final_ms') ?? item.endpointToSttFinalMs ?? null,
    providerTimeToFirstTokenMs: metric(event.payload, 'provider_time_to_first_token_ms') ?? item.providerTimeToFirstTokenMs ?? null,
    providerCompletionMs: metric(event.payload, 'provider_completion_ms') ?? item.providerCompletionMs ?? null,
    ttsTimeToFirstAudioMs: metric(event.payload, 'tts_time_to_first_audio_ms') ?? item.ttsTimeToFirstAudioMs ?? null,
    cancellationLatencyMs: metric(event.payload, 'cancellation_latency_ms') ?? item.cancellationLatencyMs ?? null,
    totalTurnMs: metric(event.payload, 'total_turn_ms') ?? item.totalTurnMs ?? null,
    slowestStage: typeof event.payload.slowest_stage === 'string' ? event.payload.slowest_stage : item.slowestStage ?? null,
    providerMode: typeof event.payload.provider_mode === 'string' ? event.payload.provider_mode : item.providerMode ?? null,
    providerIdentity: typeof event.payload.provider_identity === 'string' ? event.payload.provider_identity : item.providerIdentity ?? null,
    externalTransfer: typeof event.payload.external_transfer === 'boolean' ? event.payload.external_transfer : item.externalTransfer ?? null,
    providerInputUnitCount: count(event.payload, 'provider_input_unit_count') ?? item.providerInputUnitCount ?? null,
    providerOutputUnitCount: count(event.payload, 'provider_output_unit_count') ?? item.providerOutputUnitCount ?? null,
    providerTotalUnitCount: count(event.payload, 'provider_total_unit_count') ?? item.providerTotalUnitCount ?? null,
    pcmQueueMaxBlocks: count(event.payload, 'server_pcm_queue_max_blocks') ?? item.pcmQueueMaxBlocks ?? null,
    segmentQueueMaxSegments: count(event.payload, 'server_segment_queue_max_segments') ?? item.segmentQueueMaxSegments ?? null,
    cancellationCount: count(event.payload, 'cancellation_count') ?? item.cancellationCount ?? 0,
    staleDropCount: count(event.payload, 'stale_drop_count') ?? item.staleDropCount ?? 0,
    cpuUtilizationPercent: metric(event.payload, 'cpu_utilization_percent') ?? item.cpuUtilizationPercent ?? null,
    hostRamUsedMib: metric(event.payload, 'host_ram_used_mib') ?? item.hostRamUsedMib ?? null,
    processRssMib: metric(event.payload, 'process_rss_mib') ?? item.processRssMib ?? null,
    gpuVramUsedMib: metric(event.payload, 'gpu_vram_used_mib') ?? item.gpuVramUsedMib ?? null,
    gpuUtilizationPercent: metric(event.payload, 'gpu_utilization_percent') ?? item.gpuUtilizationPercent ?? null,
    userState: event.terminal
      ? visibleState(event.payload, event.type === 'turn.completed' ? 'available' : event.type === 'turn.interrupted' ? 'interrupted' : 'unavailable')
      : item.userState ?? 'available',
    failureStage: event.type === 'turn.failed' ? failureCode(event.payload, 'stage', 'controller') : item.failureStage ?? null,
    failureCode: event.type === 'turn.failed' ? failureCode(event.payload, 'code', 'unknown_failure') : item.failureCode ?? null,
  }))
  const error = event.type === 'turn.failed'
    ? `Ошибка ответа: ${failureCode(event.payload, 'stage', 'controller')}/${failureCode(event.payload, 'code', 'unknown_failure')}`
    : next.error
  const availability = event.type === 'turn.completed'
    ? hasLateControlDegradation(next) ? 'degraded' : 'available'
    : event.type === 'turn.interrupted'
      ? 'interrupted'
      : event.type === 'turn.failed'
        ? visibleState(event.payload, 'unavailable')
        : next.availability
  return {
    ...next,
    lastSequence: event.sequence,
    availability,
    phase,
    transcript,
    response,
    history,
    error,
    failureStage: event.type === 'turn.failed'
      ? failureCode(event.payload, 'stage', 'controller') : next.failureStage,
    failureCode: event.type === 'turn.failed'
      ? failureCode(event.payload, 'code', 'unknown_failure') : next.failureCode,
    retryCount: event.type === 'turn.failed' ? count(event.payload, 'retry_count') ?? 0 : next.retryCount,
    retryLimit: event.type === 'turn.failed' ? count(event.payload, 'retry_limit') ?? 0 : next.retryLimit,
    currentTurnTerminal: event.terminal,
    lastTurnEvent: event.type,
  }
}
