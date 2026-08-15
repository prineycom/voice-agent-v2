import type { AvatarHealthV1, AvatarLifecycleState } from '../avatar/contract'
import type { SpeechEnvelopeStatus } from '../playback'
import type {
  ConnectionState,
  HealthComponentName,
  TurnHistoryItem,
  VoiceState,
} from '../state'

export type MicrophoneVisualState = 'idle' | 'listening' | 'muted' | 'error'
export type ComponentHealth = 'READY' | 'CONNECTING' | 'DEGRADED' | 'UNAVAILABLE' | 'UNKNOWN'

export interface UiSystemComponent {
  id: 'livekit' | 'controller' | 'stt' | 'selected_llm' | 'tts' | 'avatar_host' | 'active_module'
  label: string
  health: ComponentHealth
  liveness: 'ALIVE' | 'DEAD' | 'UNKNOWN'
  readiness: 'READY' | 'UNREADY' | 'DEGRADED' | 'UNKNOWN'
  compatible: boolean | null
  reason: string | null
}

export interface VoiceUiModel {
  connection: ConnectionState
  connectionLabel: string
  avatarLifecycle: AvatarLifecycleState
  microphoneVisual: MicrophoneVisualState
  microphoneStatusLabel: string
  microphoneEnabled: boolean
  microphoneAvailable: boolean
  microphoneTransitioning: boolean
  microphoneError: boolean
  response: string
  responseComplete: boolean
  history: TurnHistoryItem[]
  components: UiSystemComponent[]
  llmProviderSummary: string
  llmModelSummary: string
  ttsSummary: string
  droppedEvents: number
  audioBlocked: boolean
  speechEnvelopeStatus: SpeechEnvelopeStatus
}

function voicePathHealth(connection: ConnectionState): ComponentHealth {
  if (connection === 'ready') return 'READY'
  if (connection === 'connecting' || connection === 'reconnecting') return 'CONNECTING'
  if (connection === 'failed') return 'UNAVAILABLE'
  if (connection === 'closed') return 'DEGRADED'
  return 'UNKNOWN'
}

function observedServerComponent(
  state: VoiceState,
  component: HealthComponentName,
  label: string,
): UiSystemComponent {
  const observation = state.health?.components.find((candidate) => candidate.component === component)
  const affected = state.failureStage === (
    component === 'selected_llm' ? 'llm_provider' : component
  )
  if (observation === undefined) {
    return {
      id: component,
      label,
      health: affected && state.availability === 'degraded'
        ? 'DEGRADED'
        : voicePathHealth(state.connection),
      liveness: 'UNKNOWN',
      readiness: 'UNKNOWN',
      compatible: null,
      reason: affected ? state.failureCode : null,
    }
  }
  const health: ComponentHealth = observation.readiness === 'ready' && observation.compatible
    ? affected && state.availability !== 'available'
      ? state.availability === 'degraded' ? 'DEGRADED' : 'UNAVAILABLE'
      : 'READY'
    : observation.readiness === 'degraded' ? 'DEGRADED' : 'UNAVAILABLE'
  return {
    id: component,
    label,
    health,
    liveness: observation.liveness.toUpperCase() as UiSystemComponent['liveness'],
    readiness: observation.readiness.toUpperCase() as UiSystemComponent['readiness'],
    compatible: observation.compatible,
    reason: affected ? state.failureCode : observation.reason_code,
  }
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
  const microphoneVisual: MicrophoneVisualState = state.microphoneStatus === 'error'
    || state.microphoneError !== null
    || state.connection === 'failed'
    ? 'error'
    : !state.microphoneEnabled
      ? 'muted'
      : state.phase === 'listening'
        ? 'listening'
        : 'idle'
  const microphoneStatusLabel = state.microphoneStatus === 'requesting-permission'
    ? 'MIC PERMISSION'
    : state.microphoneStatus === 'publishing'
      ? 'MIC PUBLISHING'
      : state.microphoneStatus === 'error'
        ? 'MIC ERROR'
        : state.microphoneTransitioning
          ? 'MIC UPDATING'
          : state.microphoneStatus === 'live'
            ? (state.phase === 'listening' ? 'MIC LISTENING' : 'MIC LIVE')
            : state.microphoneStatus === 'muted'
              ? 'MIC MUTED'
              : 'MIC DISCONNECTED'
  const visualCapabilityDegraded = avatarHealth.status !== 'ready'
    || state.speechEnvelopeStatus === 'unavailable'
  const connectionLabel: Record<ConnectionState, string> = {
    idle: 'OFFLINE',
    connecting: 'CONNECTING',
    ready: state.availability === 'available'
      ? visualCapabilityDegraded ? 'DEGRADED' : 'READY'
      : state.availability.toUpperCase(),
    reconnecting: 'RECONNECTING',
    closed: 'CLOSED',
    failed: state.availability === 'degraded' || state.availability === 'unavailable'
      ? state.availability.toUpperCase()
      : 'CONNECTION LOST',
  }
  const avatarComponentHealth: ComponentHealth = avatarHealth.status === 'failed'
    ? 'UNAVAILABLE'
    : avatarHealth.status === 'degraded' || state.speechEnvelopeStatus === 'unavailable'
      ? 'DEGRADED'
      : 'READY'

  return {
    connection: state.connection,
    connectionLabel: connectionLabel[state.connection],
    avatarLifecycle,
    microphoneVisual,
    microphoneStatusLabel,
    microphoneEnabled: state.microphoneEnabled,
    microphoneAvailable: state.microphoneAvailable,
    microphoneTransitioning: state.microphoneTransitioning,
    microphoneError: state.microphoneError !== null,
    response: state.response,
    responseComplete: state.currentTurnTerminal,
    history: state.history,
    components: [
      observedServerComponent(state, 'livekit', 'LIVEKIT'),
      observedServerComponent(state, 'controller', 'CONTROLLER'),
      observedServerComponent(state, 'stt', 'STT'),
      observedServerComponent(state, 'selected_llm', 'SELECTED LLM'),
      observedServerComponent(state, 'tts', 'TTS'),
      {
        id: 'avatar_host', label: 'AVATAR HOST', health: avatarComponentHealth,
        liveness: avatarHealth.status === 'failed' ? 'DEAD' : 'ALIVE',
        readiness: avatarHealth.status === 'ready' ? 'READY' : avatarHealth.status === 'degraded' ? 'DEGRADED' : 'UNREADY',
        compatible: avatarHealth.status !== 'failed',
        reason: avatarHealth.status === 'failed' ? 'avatar_host_unavailable' : null,
      },
      {
        id: 'active_module', label: 'ACTIVE MODULE', health: avatarComponentHealth,
        liveness: avatarHealth.status === 'failed' ? 'DEAD' : 'ALIVE',
        readiness: avatarHealth.status === 'ready' ? 'READY' : avatarHealth.status === 'degraded' ? 'DEGRADED' : 'UNREADY',
        compatible: avatarHealth.activeModuleId !== null,
        reason: avatarHealth.renderFailures > 0 ? 'render_loop_failed' : null,
      },
    ],
    llmProviderSummary: state.llmProfile === null
      ? 'UNAVAILABLE'
      : `${state.llmProfile.provider_mode.toUpperCase()} / SERVER-VERIFIED`,
    llmModelSummary: state.llmProfile?.model_identity ?? 'UNAVAILABLE',
    ttsSummary: state.ttsProfile === null
      ? 'UNAVAILABLE'
      : `${state.ttsProfile.backend.toUpperCase()} / ${state.ttsProfile.speaker} / ${state.ttsProfile.output_sample_rate_hz / 1000} KHZ`,
    droppedEvents: state.droppedEvents,
    audioBlocked: state.audioBlocked,
    speechEnvelopeStatus: state.speechEnvelopeStatus,
  }
}
