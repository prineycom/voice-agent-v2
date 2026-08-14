import type { AvatarHealthV1, AvatarLifecycleState } from '../avatar/contract'
import type { ConnectionState, TurnHistoryItem, VoiceState } from '../state'

export type MicrophoneVisualState = 'idle' | 'listening' | 'muted' | 'error'
export type ComponentHealth = 'READY' | 'CONNECTING' | 'DEGRADED' | 'FAILED' | 'UNKNOWN'

export interface UiSystemComponent {
  id: 'livekit' | 'stt' | 'llm' | 'tts' | 'avatar'
  label: string
  health: ComponentHealth
}

export interface VoiceUiModel {
  connection: ConnectionState
  connectionLabel: string
  avatarLifecycle: AvatarLifecycleState
  microphoneVisual: MicrophoneVisualState
  microphoneEnabled: boolean
  microphoneAvailable: boolean
  microphoneTransitioning: boolean
  microphoneError: boolean
  response: string
  responseComplete: boolean
  history: TurnHistoryItem[]
  components: UiSystemComponent[]
  ttsSummary: string
  droppedEvents: number
  audioBlocked: boolean
}

function voicePathHealth(connection: ConnectionState): ComponentHealth {
  if (connection === 'ready') return 'READY'
  if (connection === 'connecting' || connection === 'reconnecting') return 'CONNECTING'
  if (connection === 'failed') return 'FAILED'
  if (connection === 'closed') return 'DEGRADED'
  return 'UNKNOWN'
}

export function mapVoiceStateToUi(
  state: VoiceState,
  avatarHealth: AvatarHealthV1,
  turnFailureActive = false,
): VoiceUiModel {
  const avatarLifecycle: AvatarLifecycleState = state.connection === 'failed' || turnFailureActive
    ? 'error'
    : state.connection === 'reconnecting'
      ? 'reconnecting'
      : state.currentTurnTerminal && state.lastTurnEvent === 'turn.interrupted'
        ? 'interrupted'
        : state.phase
  const microphoneVisual: MicrophoneVisualState = state.microphoneError !== null
    || state.connection === 'failed'
    ? 'error'
    : !state.microphoneEnabled
      ? 'muted'
      : state.phase === 'listening'
        ? 'listening'
        : 'idle'
  const connectionLabel: Record<ConnectionState, string> = {
    idle: 'OFFLINE',
    connecting: 'CONNECTING',
    ready: 'READY',
    reconnecting: 'RECONNECTING',
    closed: 'CLOSED',
    failed: 'CONNECTION LOST',
  }
  const commonHealth = voicePathHealth(state.connection)
  const avatarComponentHealth: ComponentHealth = avatarHealth.status === 'ready'
    ? 'READY'
    : avatarHealth.status === 'degraded'
      ? 'DEGRADED'
      : 'FAILED'

  return {
    connection: state.connection,
    connectionLabel: connectionLabel[state.connection],
    avatarLifecycle,
    microphoneVisual,
    microphoneEnabled: state.microphoneEnabled,
    microphoneAvailable: state.microphoneAvailable,
    microphoneTransitioning: state.microphoneTransitioning,
    microphoneError: state.microphoneError !== null,
    response: state.response,
    responseComplete: state.currentTurnTerminal,
    history: state.history,
    components: [
      { id: 'livekit', label: 'LIVEKIT', health: commonHealth },
      { id: 'stt', label: 'STT', health: commonHealth },
      { id: 'llm', label: 'LLM', health: commonHealth },
      { id: 'tts', label: 'TTS', health: commonHealth },
      { id: 'avatar', label: 'AVATAR', health: avatarComponentHealth },
    ],
    ttsSummary: state.ttsProfile === null
      ? 'UNAVAILABLE'
      : `${state.ttsProfile.backend.toUpperCase()} / ${state.ttsProfile.speaker} / ${state.ttsProfile.output_sample_rate_hz / 1000} KHZ`,
    droppedEvents: state.droppedEvents,
    audioBlocked: state.audioBlocked,
  }
}
