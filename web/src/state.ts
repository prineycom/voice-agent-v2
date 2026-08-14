export const CONTROL_VERSION = 'voice-agent.realtime-control.v2'
export const CLIENT_CONTROL_VERSION = 'voice-agent.client-control.v1'
export const CONTROL_TOPIC = 'voice-agent.control.v2'
export const CLIENT_CONTROL_TOPIC = 'voice-agent.client-control.v1'

const MAX_CONTROL_BYTES = 65_536
const MAX_SEQUENCE = 1_000_000_000
const CORRELATION_ID = /^[a-z0-9][a-z0-9-]{0,63}$/

export type ConnectionState = 'idle' | 'connecting' | 'ready' | 'reconnecting' | 'closed' | 'failed'
export type TurnPhase = 'idle' | 'listening' | 'thinking' | 'speaking'
export type TurnOutcome = 'completed' | 'interrupted' | 'failed'
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

export interface SessionCapability {
  session_id: string
  stream_epoch: number
  livekit_url: string
  token: string
  expires_in_seconds: number
  admission_timeout_ms: number
  control_version: typeof CONTROL_VERSION
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
}

export interface VoiceState {
  connection: ConnectionState
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
  droppedEvents: number
  audioBlocked: boolean
  microphoneAvailable: boolean
  microphoneEnabled: boolean
  microphoneTransitioning: boolean
  microphoneError: string | null
  ttsProfile: TTSProfile | null
}

export const initialVoiceState: VoiceState = {
  connection: 'idle',
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
  droppedEvents: 0,
  audioBlocked: false,
  microphoneAvailable: false,
  microphoneEnabled: false,
  microphoneTransitioning: false,
  microphoneError: null,
  ttsProfile: null,
}

export type VoiceAction =
  | { type: 'session-created'; capability: SessionCapability }
  | { type: 'connection'; connection: ConnectionState; error?: string }
  | { type: 'control'; event: ControlEvent }
  | { type: 'drop' }
  | { type: 'audio-blocked'; blocked: boolean }
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
    if (event.type === 'turn.listening') {
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
      this.currentTurnTerminal = false
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

export function parseCapability(value: unknown): SessionCapability | null {
  if (!ownObject(value)) return null
  const keys = Object.keys(value).sort()
  const expected = ['admission_timeout_ms', 'control_version', 'expires_in_seconds', 'livekit_url', 'session_id', 'stream_epoch', 'token', 'tts_profile']
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
    || parseTTSProfile(value.tts_profile) === null
  ) return null
  return value as unknown as SessionCapability
}

function drop(state: VoiceState): VoiceState {
  return { ...state, droppedEvents: state.droppedEvents + 1 }
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
  if (action.type === 'microphone') {
    return {
      ...state,
      microphoneAvailable: true,
      microphoneEnabled: action.enabled,
      microphoneTransitioning: action.transitioning,
      microphoneError: action.error ?? null,
    }
  }
  if (action.type === 'session-created') {
    return {
      ...initialVoiceState,
      connection: 'connecting',
      sessionId: action.capability.session_id,
      streamEpoch: action.capability.stream_epoch,
      ttsProfile: action.capability.tts_profile,
    }
  }
  if (action.type === 'connection') {
    if (action.connection === 'reconnecting') {
      return {
        ...state,
        connection: 'reconnecting',
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
    return {
      ...state,
      connection: degraded ? 'failed' : 'reconnecting',
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
    return { ...state, connection: 'ready', lastSequence: event.sequence, error: null }
  }
  if (state.connection === 'reconnecting' || event.stream_epoch !== state.streamEpoch) return drop(state)
  if (event.type.startsWith('session.')) {
    if (
      event.turn_id !== 'session'
      || event.turn_generation !== 0
      || event.request_id !== 'session'
      || event.media_generation !== 0
    ) return drop(state)
    return {
      ...state,
      connection: event.type === 'session.degraded' ? 'failed' : 'ready',
      lastSequence: event.sequence,
      error: event.type === 'session.degraded' ? 'Локальный голосовой путь недоступен' : null,
    }
  }

  let next = state
  if (event.type === 'turn.listening') {
    if (!state.currentTurnTerminal || state.currentTurnId === event.turn_id) return drop(state)
    const item: TurnHistoryItem = {
      turnId: event.turn_id,
      user: '',
      assistant: '',
      outcome: null,
      audioUnavailable: false,
      endpointToFirstVisibleMs: null,
      endpointToFirstAcceptedPcmMs: null,
    }
    next = {
      ...state,
      currentTurnId: event.turn_id,
      currentTurnGeneration: event.turn_generation,
      currentRequestId: event.request_id,
      currentMediaGeneration: event.media_generation,
      currentTurnTerminal: false,
      lastTurnEvent: event.type,
      phase: 'listening',
      transcript: '',
      response: '',
      history: [...state.history, item],
      error: null,
      lastSequence: event.sequence,
    }
    return next
  }
  if (
    event.turn_id !== state.currentTurnId
    || event.turn_generation !== state.currentTurnGeneration
    || event.request_id !== state.currentRequestId
    || event.media_generation !== state.currentMediaGeneration
    || state.currentTurnTerminal
    || !validTurnTransition(state.lastTurnEvent, event.type)
  ) return drop(state)

  let phase: TurnPhase = state.phase
  if (event.type === 'stt.final' || event.type === 'turn.thinking') phase = 'thinking'
  if (event.type === 'llm.visible' && state.phase !== 'speaking') phase = 'thinking'
  if (event.type === 'turn.speaking') phase = 'speaking'
  if (event.terminal) phase = 'idle'
  const transcript = event.type === 'stt.final' && typeof event.payload.transcript === 'string'
    ? event.payload.transcript : state.transcript
  const response = event.type === 'llm.visible' && typeof event.payload.response === 'string'
    ? event.payload.response : state.response
  const outcome: TurnOutcome | null = event.type === 'turn.completed'
    ? 'completed'
    : event.type === 'turn.interrupted'
      ? 'interrupted'
      : event.type === 'turn.failed'
        ? 'failed'
        : null
  const visibleMetric = metric(event.payload, 'endpoint_to_first_visible_ms')
  const pcmMetric = metric(event.payload, 'endpoint_to_first_accepted_pcm_ms')
  const history = updateHistory(state.history, event.turn_id, (item) => ({
    ...item,
    user: transcript,
    assistant: response,
    outcome: outcome ?? item.outcome,
    audioUnavailable: item.audioUnavailable || (
      event.type === 'turn.failed' && event.payload.stage === 'tts'
    ),
    endpointToFirstVisibleMs: visibleMetric ?? item.endpointToFirstVisibleMs,
    endpointToFirstAcceptedPcmMs: pcmMetric ?? item.endpointToFirstAcceptedPcmMs,
  }))
  const error = event.type === 'turn.failed'
    ? `Ошибка ответа: ${String(event.payload.stage ?? 'turn')}/${String(event.payload.code ?? 'unknown')}`
    : state.error
  return {
    ...state,
    lastSequence: event.sequence,
    phase,
    transcript,
    response,
    history,
    error,
    currentTurnTerminal: event.terminal,
    lastTurnEvent: event.type,
  }
}
