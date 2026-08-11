export const CONTROL_VERSION = 'voice-agent.realtime-control.v1'
export const CLIENT_CONTROL_VERSION = 'voice-agent.client-control.v1'
export const CONTROL_TOPIC = 'voice-agent.control.v1'
export const CLIENT_CONTROL_TOPIC = 'voice-agent.client-control.v1'

const MAX_CONTROL_BYTES = 65_536
const MAX_SEQUENCE = 1_000_000_000
const CORRELATION_ID = /^[a-z0-9][a-z0-9-]{0,63}$/

export type ConnectionState = 'idle' | 'connecting' | 'ready' | 'reconnecting' | 'closed' | 'failed'
export type TurnPhase = 'idle' | 'listening' | 'transcribing' | 'thinking' | 'speaking' | 'completed' | 'interrupted' | 'failed'
export type ControlEventType =
  | 'session.ready'
  | 'session.reconnected'
  | 'session.degraded'
  | 'turn.listening'
  | 'turn.transcribing'
  | 'stt.final'
  | 'turn.thinking'
  | 'llm.final'
  | 'turn.speaking'
  | 'turn.playout-ready'
  | 'turn.completed'
  | 'turn.interrupted'
  | 'turn.failed'

const EVENT_TYPES = new Set<ControlEventType>([
  'session.ready', 'session.reconnected', 'session.degraded',
  'turn.listening', 'turn.transcribing', 'stt.final', 'turn.thinking',
  'llm.final', 'turn.speaking', 'turn.playout-ready', 'turn.completed',
  'turn.interrupted', 'turn.failed',
])
const TERMINAL_TYPES = new Set<ControlEventType>([
  'turn.completed', 'turn.interrupted', 'turn.failed',
])
const TURN_PREDECESSOR = new Map<ControlEventType, ControlEventType>([
  ['turn.transcribing', 'turn.listening'],
  ['stt.final', 'turn.transcribing'],
  ['turn.thinking', 'stt.final'],
  ['llm.final', 'turn.thinking'],
  ['turn.speaking', 'llm.final'],
  ['turn.playout-ready', 'turn.speaking'],
  ['turn.completed', 'turn.playout-ready'],
])

export interface ControlEvent {
  schema_version: typeof CONTROL_VERSION
  session_id: string
  turn_id: string
  stream_epoch: number
  sequence: number
  type: ControlEventType
  terminal: boolean
  payload: Record<string, unknown>
}

export interface SessionCapability {
  session_id: string
  stream_epoch: number
  livekit_url: string
  token: string
  expires_in_seconds: number
  control_version: typeof CONTROL_VERSION
}

export interface VoiceState {
  connection: ConnectionState
  sessionId: string | null
  streamEpoch: number
  lastSequence: number
  currentTurnId: string | null
  currentTurnTerminal: boolean
  lastTurnEvent: ControlEventType | null
  phase: TurnPhase
  transcript: string
  response: string
  error: string | null
  droppedEvents: number
  audioBlocked: boolean
}

export const initialVoiceState: VoiceState = {
  connection: 'idle',
  sessionId: null,
  streamEpoch: 0,
  lastSequence: 0,
  currentTurnId: null,
  currentTurnTerminal: true,
  lastTurnEvent: null,
  phase: 'idle',
  transcript: '',
  response: '',
  error: null,
  droppedEvents: 0,
  audioBlocked: false,
}

export type VoiceAction =
  | { type: 'session-created'; capability: SessionCapability }
  | { type: 'connection'; connection: ConnectionState; error?: string }
  | { type: 'control'; event: ControlEvent }
  | { type: 'drop' }
  | { type: 'audio-blocked'; blocked: boolean }
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
  const expected = ['payload', 'schema_version', 'sequence', 'session_id', 'stream_epoch', 'terminal', 'turn_id', 'type']
  if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) return null
  const eventType = value.type
  if (
    value.schema_version !== CONTROL_VERSION ||
    typeof value.session_id !== 'string' || !CORRELATION_ID.test(value.session_id) ||
    typeof value.turn_id !== 'string' || !CORRELATION_ID.test(value.turn_id) ||
    !Number.isSafeInteger(value.stream_epoch) || (value.stream_epoch as number) < 1 || (value.stream_epoch as number) > MAX_SEQUENCE ||
    !Number.isSafeInteger(value.sequence) || (value.sequence as number) < 1 || (value.sequence as number) > MAX_SEQUENCE ||
    typeof eventType !== 'string' || !EVENT_TYPES.has(eventType as ControlEventType) ||
    typeof value.terminal !== 'boolean' || value.terminal !== TERMINAL_TYPES.has(eventType as ControlEventType) ||
    !ownObject(value.payload) || !boundedValue(value.payload)
  ) return null
  return value as unknown as ControlEvent
}

export class RealtimeControlGate {
  private streamEpoch: number
  private lastSequence = 0
  private currentTurnId: string | null = null
  private currentTurnTerminal = true
  private lastTurnEvent: ControlEventType | null = null
  private reconnecting = false

  constructor(private readonly sessionId: string, streamEpoch: number) {
    this.streamEpoch = streamEpoch
  }

  beginReconnect(): void {
    this.reconnecting = true
    this.currentTurnId = null
    this.currentTurnTerminal = true
    this.lastTurnEvent = null
  }

  accept(event: ControlEvent): boolean {
    if (event.session_id !== this.sessionId || event.sequence <= this.lastSequence) return false
    if (
      this.reconnecting &&
      (event.type === 'session.reconnected' || event.type === 'session.degraded')
    ) {
      if (event.turn_id !== 'session' || event.stream_epoch !== this.streamEpoch + 1) return false
      this.streamEpoch = event.stream_epoch
      this.lastSequence = event.sequence
      this.reconnecting = false
      return true
    }
    if (this.reconnecting || event.stream_epoch !== this.streamEpoch) return false
    if (event.type.startsWith('session.')) {
      if (event.turn_id !== 'session') return false
      this.lastSequence = event.sequence
      return true
    }
    if (event.type === 'turn.listening') {
      if (!this.currentTurnTerminal || this.currentTurnId === event.turn_id) return false
      this.currentTurnId = event.turn_id
      this.currentTurnTerminal = false
      this.lastTurnEvent = event.type
    } else {
      if (
        event.turn_id !== this.currentTurnId || this.currentTurnTerminal ||
        !validTurnTransition(this.lastTurnEvent, event.type)
      ) return false
      this.lastTurnEvent = event.type
      if (event.terminal) this.currentTurnTerminal = true
    }
    this.lastSequence = event.sequence
    return true
  }
}

export function parseCapability(value: unknown): SessionCapability | null {
  if (!ownObject(value)) return null
  const keys = Object.keys(value).sort()
  const expected = ['control_version', 'expires_in_seconds', 'livekit_url', 'session_id', 'stream_epoch', 'token']
  if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) return null
  if (
    typeof value.session_id !== 'string' || !CORRELATION_ID.test(value.session_id) ||
    value.stream_epoch !== 1 ||
    typeof value.livekit_url !== 'string' || !value.livekit_url.startsWith('wss://') || value.livekit_url.length > 2048 ||
    typeof value.token !== 'string' || value.token.length < 16 || value.token.length > 8192 ||
    !Number.isSafeInteger(value.expires_in_seconds) || (value.expires_in_seconds as number) < 1 || (value.expires_in_seconds as number) > 600 ||
    value.control_version !== CONTROL_VERSION
  ) return null
  return value as unknown as SessionCapability
}

function drop(state: VoiceState): VoiceState {
  return { ...state, droppedEvents: state.droppedEvents + 1 }
}

function validTurnTransition(previous: ControlEventType | null, next: ControlEventType): boolean {
  if (next === 'turn.interrupted' || next === 'turn.failed') return previous !== null
  return TURN_PREDECESSOR.get(next) === previous
}

function phaseFor(type: ControlEventType): TurnPhase | null {
  if (type === 'turn.listening') return 'listening'
  if (type === 'turn.transcribing' || type === 'stt.final') return 'transcribing'
  if (type === 'turn.thinking' || type === 'llm.final') return 'thinking'
  if (type === 'turn.speaking' || type === 'turn.playout-ready') return 'speaking'
  if (type === 'turn.completed') return 'completed'
  if (type === 'turn.interrupted') return 'interrupted'
  if (type === 'turn.failed') return 'failed'
  return null
}

export function voiceReducer(state: VoiceState, action: VoiceAction): VoiceState {
  if (action.type === 'reset') return initialVoiceState
  if (action.type === 'drop') return drop(state)
  if (action.type === 'audio-blocked') return { ...state, audioBlocked: action.blocked }
  if (action.type === 'session-created') {
    return {
      ...initialVoiceState,
      connection: 'connecting',
      sessionId: action.capability.session_id,
      streamEpoch: action.capability.stream_epoch,
    }
  }
  if (action.type === 'connection') {
    if (action.connection === 'reconnecting') {
      return {
        ...state,
        connection: 'reconnecting',
        currentTurnId: null,
        currentTurnTerminal: true,
        lastTurnEvent: null,
        phase: 'idle',
        transcript: '',
        response: '',
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
  let next = state
  if (
    state.connection === 'reconnecting' &&
    (event.type === 'session.reconnected' || event.type === 'session.degraded')
  ) {
    if (event.turn_id !== 'session' || event.stream_epoch !== state.streamEpoch + 1) return drop(state)
    const degraded = event.type === 'session.degraded'
    return {
      ...state,
      connection: degraded ? 'failed' : 'ready',
      streamEpoch: event.stream_epoch,
      lastSequence: event.sequence,
      currentTurnId: null,
      currentTurnTerminal: true,
      lastTurnEvent: null,
      phase: degraded ? 'failed' : 'idle',
      transcript: '',
      response: '',
      error: degraded ? 'Не удалось безопасно восстановить сессию' : null,
    }
  }
  if (state.connection === 'reconnecting' || event.stream_epoch !== state.streamEpoch) return drop(state)
  if (event.type.startsWith('session.')) {
    if (event.turn_id !== 'session') return drop(state)
    return {
      ...state,
      connection: event.type === 'session.degraded' ? 'failed' : 'ready',
      lastSequence: event.sequence,
      error: event.type === 'session.degraded' ? 'Локальный голосовой путь недоступен' : null,
    }
  }
  if (event.type === 'turn.listening') {
    if (!state.currentTurnTerminal || state.currentTurnId === event.turn_id) return drop(state)
    next = {
      ...state,
      currentTurnId: event.turn_id,
      currentTurnTerminal: false,
      lastTurnEvent: event.type,
      transcript: '',
      response: '',
      error: null,
    }
  } else if (
    event.turn_id !== state.currentTurnId || state.currentTurnTerminal ||
    !validTurnTransition(state.lastTurnEvent, event.type)
  ) {
    return drop(state)
  }
  const phase = phaseFor(event.type)
  if (phase === null) return drop(state)
  const transcript = event.type === 'stt.final' && typeof event.payload.transcript === 'string'
    ? event.payload.transcript : next.transcript
  const response = event.type === 'llm.final' && typeof event.payload.response === 'string'
    ? event.payload.response : next.response
  const error = event.type === 'turn.failed'
    ? `Ошибка: ${String(event.payload.stage ?? 'turn')}/${String(event.payload.code ?? 'unknown')}`
    : next.error
  return {
    ...next,
    lastSequence: event.sequence,
    phase,
    transcript,
    response,
    error,
    currentTurnTerminal: event.terminal,
    lastTurnEvent: event.type,
  }
}
