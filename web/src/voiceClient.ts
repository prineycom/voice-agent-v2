import {
  Room,
  RoomEvent,
  Track,
  createLocalAudioTrack,
  type LocalAudioTrack,
  type RemoteAudioTrack,
} from 'livekit-client'
import {
  AudioPlaybackBoundary,
  type SpeechEnvelopeObservation,
  type SpeechEnvelopeStatus,
} from './playback'
import {
  CLIENT_CONTROL_TOPIC,
  CLIENT_CONTROL_VERSION,
  CONTROL_TOPIC,
  RealtimeControlGate,
  parseCapability,
  parseControlEvent,
  type ConnectionState,
  type ControlEvent,
  type MicrophoneLifecycle,
  type SessionCapability,
} from './state'

const RECONNECT_ACK_TIMEOUT_MS = 5_000
const RECONNECT_RETRY_INTERVAL_MS = 500
const SAFE_DIAGNOSTIC_CODE = /^[a-z0-9_]{1,64}$/

function privacySafeCode(value: unknown, fallback: string): string {
  return typeof value === 'string' && SAFE_DIAGNOSTIC_CODE.test(value) ? value : fallback
}

export interface VoiceDiagnosticRecord {
  schemaVersion: 'voice-agent.browser-observation.v1'
  timestamp: string
  monotonicMs: number
  recordSequence: number
  stage: string
  event: string
  sessionId: string | null
  turnId: string | null
  requestId: string | null
  streamEpoch: number
  mediaGeneration: number | null
  sequence: number | null
  serverControlType?: string
  userState?: string
  failureStage?: string
  failureCode?: string
  failureClass?: string
  providerMode?: string
  externalTransfer?: boolean
  providerInputUnitCount?: number
  providerOutputUnitCount?: number
  providerTotalUnitCount?: number
  endpointToSttFinalMs?: number
  providerTimeToFirstTokenMs?: number
  providerCompletionMs?: number
  ttsTimeToFirstAudioMs?: number
  cancellationLatencyMs?: number
  totalTurnMs?: number
  cpuUtilizationPercent?: number
  hostRamUsedMib?: number
  gpuVramUsedMib?: number
}

export interface VoiceClientCallbacks {
  onSession(capability: SessionCapability): void
  onConnection(connection: ConnectionState, error?: string): void
  onControl(event: ControlEvent): void
  onDrop(): void
  onAudioBlocked(blocked: boolean): void
  onSpeechEnvelope(observation: SpeechEnvelopeObservation): void
  onSpeechEnvelopeStatus(status: SpeechEnvelopeStatus): void
  onMicrophoneLifecycle(status: MicrophoneLifecycle): void
  onMicrophoneState(enabled: boolean, transitioning: boolean, error?: string): void
  onDiagnostic?(record: VoiceDiagnosticRecord): void
}

export class VoiceClient {
  private room: Room | null = null
  private microphone: LocalAudioTrack | null = null
  private playback: AudioPlaybackBoundary
  private capability: SessionCapability | null = null
  private controlGate: RealtimeControlGate | null = null
  private clientSequence = 0
  private stopping = false
  private startAbort: AbortController | null = null
  private stopPromise: Promise<void> | null = null
  private reconnectAckTimer: ReturnType<typeof setTimeout> | null = null
  private reconnectRetryTimer: ReturnType<typeof setTimeout> | null = null
  private initialReadyTimer: ReturnType<typeof setTimeout> | null = null
  private reconnecting = false
  private reconnectRequest: {
    room: Room
    payload: Uint8Array<ArrayBuffer>
  } | null = null
  private activeRemoteTrack: RemoteAudioTrack | null = null
  private readonly remoteTracks = new Map<string, RemoteAudioTrack>()
  private desiredMedia: {
    publicationId: string
    turnId: string
    mediaGeneration: number
    turnGeneration: number
    requestId: string
    terminal: boolean
    firstSignalObserved: boolean
  } | null = null
  private streamEpoch = 0
  private microphoneEnabled = false
  private microphoneRequested = true
  private microphoneFailureActive = false
  private microphoneTransition: Promise<void> | null = null
  private readonly diagnostics: VoiceDiagnosticRecord[] = []
  private readonly diagnosticsStarted = performance.now()
  private diagnosticSequence = 0

  constructor(
    audioContainer: HTMLElement,
    private readonly callbacks: VoiceClientCallbacks,
  ) {
    this.playback = new AudioPlaybackBoundary(
      audioContainer,
      callbacks.onAudioBlocked,
      (observation) => {
        callbacks.onSpeechEnvelope(observation)
        const desired = this.desiredMedia
        if (observation.playoutActive && desired !== null && !desired.firstSignalObserved) {
          desired.firstSignalObserved = true
          this.recordDiagnostic('playback', 'first_programmatic_signal')
        }
      },
      callbacks.onSpeechEnvelopeStatus,
    )
  }

  async start(): Promise<void> {
    if (this.stopping) throw new Error('voice session start was cancelled')
    const abort = new AbortController()
    this.startAbort = abort
    let startupStage = 'session_request'
    this.callbacks.onConnection('connecting')
    try {
      const response = await fetch('/api/session', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
        signal: abort.signal,
      })
      this.ensureStarting()
      if (!response.ok) throw new Error(`local voice path unavailable (${response.status})`)
      startupStage = 'capability'
      const capability = parseCapability(await response.json())
      this.ensureStarting()
      if (capability === null) throw new Error('invalid room capability response')
      this.capability = capability
      this.streamEpoch = capability.stream_epoch
      this.controlGate = new RealtimeControlGate(capability.session_id, capability.stream_epoch)
      this.callbacks.onSession(capability)
      this.recordDiagnostic('session', 'capability_received')

      startupStage = 'agent_connection'
      const room = new Room({ adaptiveStream: false, dynacast: false, disconnectOnPageLeave: true })
      this.room = room
      this.registerRoomHandlers(room)
      this.armInitialReadyTimeout(capability.admission_timeout_ms)
      await room.connect(capability.livekit_url, capability.token, { autoSubscribe: true })
      await this.ensureRoomStarting(room)
      if (!this.hasExpectedAgent(room)) throw new Error('voice session agent is unavailable')

      startupStage = 'microphone'
      this.callbacks.onMicrophoneLifecycle('requesting-permission')
      const microphone = await createLocalAudioTrack({
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      })
      this.microphone = microphone
      this.callbacks.onMicrophoneLifecycle('publishing')
      await this.ensureRoomStarting(room, microphone)
      startupStage = 'microphone_publication'
      await room.localParticipant.publishTrack(microphone, {
        source: Track.Source.Microphone,
        dtx: false,
        red: true,
      })
      await this.ensureRoomStarting(room, microphone)
      this.microphoneEnabled = !microphone.isMuted
      this.microphoneRequested = this.microphoneEnabled
      this.microphoneFailureActive = false
      this.callbacks.onMicrophoneLifecycle(this.microphoneEnabled ? 'live' : 'muted')
      this.callbacks.onMicrophoneState(this.microphoneEnabled, false)
    } catch (error) {
      if (startupStage === 'microphone' || startupStage === 'microphone_publication') {
        this.microphoneFailureActive = true
        this.callbacks.onMicrophoneLifecycle('error')
      }
      this.recordDiagnostic(
        startupStage,
        'failed',
        undefined,
        `startup_${startupStage}_failed`,
        error,
      )
      throw error
    } finally {
      if (this.startAbort === abort) this.startAbort = null
    }
  }

  downloadDiagnostics(): void {
    const payload = `${this.diagnostics.map((record) => JSON.stringify(record)).join('\n')}\n`
    const blob = new Blob([payload], { type: 'application/x-ndjson' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `voice-agent-diagnostic-${new Date().toISOString().replaceAll(':', '-')}.jsonl`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  async resumeAudio(): Promise<void> {
    if (this.room === null) return
    await this.room.startAudio()
    await this.playback.resume()
  }

  async setMicrophoneEnabled(enabled: boolean): Promise<void> {
    if (this.microphone === null || this.stopping) return
    this.microphoneRequested = enabled
    this.callbacks.onMicrophoneLifecycle(this.microphoneEnabled ? 'live' : 'muted')
    this.callbacks.onMicrophoneState(this.microphoneEnabled, true)
    if (this.microphoneTransition !== null) return this.microphoneTransition
    const transition = this.runMicrophoneTransitions()
    this.microphoneTransition = transition
    try {
      await transition
    } finally {
      if (this.microphoneTransition === transition) this.microphoneTransition = null
    }
  }

  async toggleMicrophone(): Promise<void> {
    return this.setMicrophoneEnabled(!this.microphoneRequested)
  }

  resetPlayback(): void {
    this.playback.reset()
  }

  async stop(): Promise<void> {
    this.stopping = true
    this.startAbort?.abort()
    return this.beginResourceRelease(true, this.microphoneFailureActive)
  }

  private beginResourceRelease(
    notifyClosed: boolean,
    preserveMicrophoneError = false,
  ): Promise<void> {
    if (this.stopPromise !== null) return this.stopPromise
    const cleanup = this.releaseResources(notifyClosed, preserveMicrophoneError)
    const tracked = cleanup.finally(() => {
      if (this.stopPromise === tracked) this.stopPromise = null
    })
    this.stopPromise = tracked
    return tracked
  }

  private async releaseResources(
    notifyClosed: boolean,
    preserveMicrophoneError: boolean,
  ): Promise<void> {
    this.clearReconnectTimers()
    this.clearInitialReadyTimer()
    this.capability = null
    this.controlGate = null
    this.reconnecting = false
    this.reconnectRequest = null
    this.activeRemoteTrack = null
    this.remoteTracks.clear()
    this.desiredMedia = null
    this.streamEpoch = 0
    const errors: unknown[] = []
    const microphone = this.microphone
    let microphoneCleanupFailed = false
    if (microphone !== null) {
      if (this.microphone === microphone) this.microphone = null
      this.microphoneEnabled = false
      this.microphoneRequested = true
      try {
        microphone.stop()
      } catch (error) {
        microphoneCleanupFailed = true
        errors.push(error)
      }
    } else {
      this.microphoneEnabled = false
      this.microphoneRequested = true
    }
    const microphoneFailed = preserveMicrophoneError || microphoneCleanupFailed
    this.microphoneFailureActive = microphoneFailed
    this.callbacks.onMicrophoneState(false, false)
    this.callbacks.onMicrophoneLifecycle(microphoneFailed ? 'error' : 'disconnected')
    try {
      await this.playback.dispose()
    } catch (error) {
      errors.push(error)
    }
    const room = this.room
    if (room !== null) {
      try {
        await room.disconnect()
        if (this.room === room) this.room = null
      } catch (error) {
        errors.push(error)
      }
    }
    if (errors.length > 0) {
      const cleanupError = new AggregateError(errors, 'voice session resource cleanup failed')
      this.recordDiagnostic('cleanup', 'failed', undefined, 'resource_cleanup_failed', cleanupError)
      if (notifyClosed) {
        this.callbacks.onConnection(
          'failed',
          'Не удалось полностью освободить ресурсы голосовой сессии',
        )
      }
      throw cleanupError
    }
    if (notifyClosed) this.callbacks.onConnection('closed')
  }

  private ensureStarting(): void {
    if (this.stopping) throw new Error('voice session start was cancelled')
  }

  private async runMicrophoneTransitions(): Promise<void> {
    let transitionError: string | undefined
    while (
      !this.stopping
      && this.microphone !== null
      && this.microphoneRequested !== this.microphoneEnabled
    ) {
      const microphone = this.microphone
      const target = this.microphoneRequested
      let failure: unknown
      try {
        if (target) await microphone.unmute()
        else await microphone.mute()
      } catch (error) {
        failure = error
      }
      if (this.stopping || this.microphone !== microphone) return
      this.microphoneEnabled = !microphone.isMuted
      if (failure !== undefined) {
        if (this.microphoneRequested !== target) {
          this.callbacks.onMicrophoneState(
            this.microphoneEnabled,
            this.microphoneRequested !== this.microphoneEnabled,
          )
          continue
        }
        this.microphoneRequested = this.microphoneEnabled
        transitionError = target
          ? 'Не удалось включить микрофон. Проверьте доступ к устройству и повторите.'
          : 'Не удалось выключить микрофон. Повторите попытку.'
        this.recordDiagnostic(
          'microphone', 'transition_failed', undefined, 'microphone_transition_failed', failure,
        )
        break
      }
      if (this.microphoneEnabled !== target) {
        if (this.microphoneRequested === this.microphoneEnabled) continue
        this.microphoneRequested = this.microphoneEnabled
        transitionError = 'Состояние микрофона не изменилось. Проверьте устройство и повторите.'
        this.recordDiagnostic(
          'microphone', 'transition_failed', undefined, 'microphone_state_unchanged', target,
        )
        break
      }
      this.callbacks.onMicrophoneState(
        this.microphoneEnabled,
        this.microphoneRequested !== this.microphoneEnabled,
      )
    }
    if (!this.stopping && this.microphone !== null) {
      this.callbacks.onMicrophoneLifecycle(
        transitionError === undefined ? (this.microphoneEnabled ? 'live' : 'muted') : 'error',
      )
      this.callbacks.onMicrophoneState(this.microphoneEnabled, false, transitionError)
    }
  }

  private async ensureRoomStarting(room: Room, microphone?: LocalAudioTrack): Promise<void> {
    if (!this.stopping) return
    if (this.room === null) this.room = room
    if (microphone !== undefined && this.microphone === null) this.microphone = microphone
    await this.stop()
    throw new Error('voice session start was cancelled')
  }

  private registerRoomHandlers(room: Room): void {
    room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
      if (track.kind !== Track.Kind.Audio || !this.isExpectedAgent(participant.identity)) return
      const publicationId = publication.trackSid
      if (typeof publicationId !== 'string' || !publicationId || publicationId.length > 128) return
      const remoteTrack = track as RemoteAudioTrack
      this.remoteTracks.set(publicationId, remoteTrack)
      this.activeRemoteTrack = remoteTrack
      const desired = this.desiredMedia
      if (
        this.reconnecting
        || desired === null
        || desired.publicationId !== publicationId
      ) {
        this.recordDiagnostic('playback', 'publication_deferred')
        return
      }
      this.playback.setTrack(remoteTrack, desired.mediaGeneration)
      this.recordDiagnostic('playback', 'publication_generation_attached')
      if (desired.terminal && this.playback.finishGeneration(desired.mediaGeneration)) {
        this.recordDiagnostic('playback', 'deferred_generation_drain_started')
      }
    })
    room.on(RoomEvent.TrackUnsubscribed, (track, publication, participant) => {
      if (track.kind !== Track.Kind.Audio || !this.isExpectedAgent(participant.identity)) return
      const publicationId = publication.trackSid
      if (typeof publicationId === 'string') this.remoteTracks.delete(publicationId)
      if (this.activeRemoteTrack === track) this.activeRemoteTrack = null
      const wasDesired = this.desiredMedia?.publicationId === publicationId
      if (wasDesired) {
        this.desiredMedia = null
        this.playback.clear()
        this.recordDiagnostic('playback', 'publication_generation_detached')
      }
      if (wasDesired && !this.reconnecting && !this.stopping) {
        void this.failSession('Аудиопоток агента недоступен', 'persistent_track_lost')
      }
    })
    room.on(RoomEvent.DataReceived, (payload, participant, _kind, topic) => {
      if (
        this.capability === null
        || topic !== CONTROL_TOPIC
        || participant?.identity !== `agent-${this.capability.session_id}`
      ) return
      const event = parseControlEvent(payload)
      if (event === null || this.controlGate === null || !this.controlGate.accept(event)) {
        this.recordDiagnostic('control', 'late_or_duplicate_event_dropped')
        this.callbacks.onDrop()
        return
      }
      let terminalFailure: string | null = null
      if (event.type === 'turn.listening') {
        try {
          this.playback.suspend()
          this.desiredMedia = null
          this.recordDiagnostic('playback', 'valid_turn_start_suspended_old_generation')
        } catch (error) {
          terminalFailure = 'Не удалось немедленно остановить старое аудио'
          this.recordDiagnostic(
            'playback', 'interrupt_suspend_failed', event, 'playback_interrupt_failed', error,
          )
        }
      } else if (event.type === 'turn.interrupted') {
        const desired = this.desiredMedia
        if (desired !== null && desired.mediaGeneration === event.media_generation) {
          try {
            this.playback.suspend(event.media_generation)
            this.desiredMedia = null
            this.recordDiagnostic('playback', 'valid_interrupt_suspended_generation')
          } catch (error) {
            terminalFailure = 'Не удалось немедленно остановить прерванное аудио'
            this.recordDiagnostic(
              'playback', 'interrupt_suspend_failed', event, 'playback_interrupt_failed', error,
            )
          }
        }
      } else if (event.type === 'turn.media-ready') {
        const publicationId = event.payload.server_media_publication_id
        const audio = event.payload.audio
        const tts = event.payload.tts
        const profile = this.capability.tts_profile
        const validAudio = (
          typeof audio === 'object' && audio !== null
          && (audio as Record<string, unknown>).encoding === 'pcm_s16le'
          && (audio as Record<string, unknown>).sample_rate_hz === 48_000
          && (audio as Record<string, unknown>).channels === 1
          && (audio as Record<string, unknown>).sample_width_bytes === 2
        )
        const validTTS = (
          typeof tts === 'object' && tts !== null
          && (tts as Record<string, unknown>).profile === profile.profile
          && (tts as Record<string, unknown>).backend === profile.backend
          && (tts as Record<string, unknown>).speaker === profile.speaker
          && (tts as Record<string, unknown>).output_sample_rate_hz === 48_000
        )
        if (
          typeof publicationId !== 'string'
          || !publicationId
          || publicationId.length > 128
          || !validAudio
          || !validTTS
        ) {
          terminalFailure = 'Сервер прислал несовместимое описание аудио'
        } else {
          this.desiredMedia = {
            publicationId,
            turnId: event.turn_id,
            mediaGeneration: event.media_generation,
            turnGeneration: event.turn_generation,
            requestId: event.request_id,
            terminal: false,
            firstSignalObserved: false,
          }
          const remoteTrack = this.remoteTracks.get(publicationId)
          if (remoteTrack !== undefined && !this.reconnecting) {
            try {
              this.playback.setTrack(remoteTrack, event.media_generation)
              this.recordDiagnostic('playback', 'publication_generation_attached')
            } catch (error) {
              terminalFailure = 'Не удалось подключить актуальное аудио'
              this.recordDiagnostic(
                'playback', 'attach_failed', event, 'playback_attach_failed', error,
              )
            }
          }
        }
      } else if (event.type === 'turn.completed' || event.type === 'turn.failed') {
        const desired = this.desiredMedia
        if (desired !== null && desired.mediaGeneration === event.media_generation) {
          desired.terminal = true
          if (this.playback.finishGeneration(event.media_generation)) {
            this.recordDiagnostic('playback', 'generation_drain_started')
          } else {
            this.recordDiagnostic('playback', 'generation_drain_deferred')
          }
        }
      } else if (event.type === 'session.ready') {
        this.clearInitialReadyTimer()
        this.clearReconnectTimers()
        this.reconnectRequest = null
        this.reconnecting = false
      } else if (event.type === 'session.reconnected') {
        this.streamEpoch = event.stream_epoch
      } else if (event.type === 'session.degraded') {
        this.streamEpoch = event.stream_epoch
        const stage = typeof event.payload.stage === 'string' ? event.payload.stage : 'session'
        const code = typeof event.payload.code === 'string' ? event.payload.code : 'degraded'
        terminalFailure = `Голосовая сессия остановлена (${stage}/${code})`
      }
      const serverFailure = event.type === 'turn.failed' || event.type === 'session.degraded'
      const serverStage = serverFailure
        ? privacySafeCode(
          event.payload.stage,
          event.type === 'session.degraded' ? 'session' : 'controller',
        )
        : 'control'
      const serverCode = serverFailure
        ? privacySafeCode(
          event.payload.code,
          event.type === 'session.degraded' ? 'degraded' : 'unknown_failure',
        )
        : undefined
      this.recordDiagnostic(serverStage, event.type, event, serverCode)
      try {
        this.callbacks.onControl(event)
      } catch {
        terminalFailure ??= 'Не удалось применить состояние голосовой сессии'
      }
      if (terminalFailure !== null) {
        if (event.type === 'session.degraded') {
          void this.failSession(
            terminalFailure,
            serverCode,
            undefined,
            false,
            false,
          )
        } else {
          void this.failSession(terminalFailure)
        }
      }
    })
    room.on(RoomEvent.Reconnecting, () => {
      if (this.reconnecting) return
      this.clearReconnectTimers()
      this.reconnecting = true
      this.reconnectRequest = null
      this.desiredMedia = null
      try {
        this.playback.suspend()
      } catch (error) {
        this.recordDiagnostic('playback', 'suspend_failed', undefined, 'playback_suspend_failed', error)
      }
      this.controlGate?.beginReconnect()
      this.callbacks.onConnection('reconnecting')
    })
    room.on(RoomEvent.Reconnected, () => {
      void this.publishReconnect()
    })
    room.on(RoomEvent.Disconnected, () => {
      this.clearReconnectTimers()
      if (!this.stopping) void this.failSession('Соединение с голосовой сессией потеряно')
    })
    room.on(RoomEvent.ParticipantDisconnected, (participant) => {
      if (this.isExpectedAgent(participant.identity)) {
        void this.failSession('Агент голосовой сессии отключился')
      }
    })
    room.on(RoomEvent.MediaDevicesError, () => {
      this.microphoneFailureActive = true
      this.callbacks.onMicrophoneLifecycle('error')
      void this.failSession('Микрофон недоступен', 'microphone_device_failed', undefined, true)
    })
  }

  private async publishReconnect(): Promise<void> {
    if (this.room === null || this.capability === null || !this.reconnecting) return
    if (this.reconnectRequest !== null) return
    this.clientSequence += 1
    const request = {
      room: this.room,
      payload: new TextEncoder().encode(JSON.stringify({
        schema_version: CLIENT_CONTROL_VERSION,
        session_id: this.capability.session_id,
        stream_epoch: this.streamEpoch,
        sequence: this.clientSequence,
        type: 'client.reconnected',
      })),
    }
    this.reconnectRequest = request
    this.clearReconnectTimers()
    this.reconnectAckTimer = setTimeout(() => {
      if (this.reconnectRequest === request) {
        void this.failSession(
          'Сервер не подтвердил восстановление сессии',
          'reconnect_ack_timeout',
        )
      }
    }, RECONNECT_ACK_TIMEOUT_MS)
    await this.sendReconnectAttempt(request)
  }

  private async sendReconnectAttempt(request: {
    room: Room
    payload: Uint8Array<ArrayBuffer>
  }): Promise<void> {
    try {
      await request.room.localParticipant.publishData(request.payload, {
        reliable: true,
        topic: CLIENT_CONTROL_TOPIC,
      })
    } catch (error) {
      if (this.reconnectRequest === request && !this.stopping) {
        this.recordDiagnostic(
          'reconnect', 'publish_retry_failed', undefined, 'reconnect_publish_failed', error,
        )
      }
    } finally {
      if (this.reconnectRequest === request && this.reconnecting && !this.stopping) {
        if (this.reconnectRetryTimer !== null) clearTimeout(this.reconnectRetryTimer)
        this.reconnectRetryTimer = setTimeout(() => {
          this.reconnectRetryTimer = null
          void this.sendReconnectAttempt(request)
        }, RECONNECT_RETRY_INTERVAL_MS)
      }
    }
  }

  private isExpectedAgent(identity: string): boolean {
    return this.capability !== null && identity === `agent-${this.capability.session_id}`
  }

  private hasExpectedAgent(room: Room): boolean {
    if (this.capability === null) return false
    return room.remoteParticipants.has(`agent-${this.capability.session_id}`)
  }

  private armInitialReadyTimeout(timeoutMs: number): void {
    this.clearInitialReadyTimer()
    this.initialReadyTimer = setTimeout(() => {
      void this.failSession('Сервер не подтвердил готовность голосовой сессии')
    }, timeoutMs)
  }

  private clearInitialReadyTimer(): void {
    if (this.initialReadyTimer === null) return
    clearTimeout(this.initialReadyTimer)
    this.initialReadyTimer = null
  }

  private clearReconnectTimers(): void {
    if (this.reconnectAckTimer !== null) clearTimeout(this.reconnectAckTimer)
    if (this.reconnectRetryTimer !== null) clearTimeout(this.reconnectRetryTimer)
    this.reconnectAckTimer = null
    this.reconnectRetryTimer = null
  }

  private recordDiagnostic(
    stage: string,
    event: string,
    control?: ControlEvent,
    failureCode?: string,
    failure?: unknown,
  ): void {
    this.diagnosticSequence += 1
    const record: VoiceDiagnosticRecord = {
      schemaVersion: 'voice-agent.browser-observation.v1',
      timestamp: new Date().toISOString(),
      monotonicMs: Math.max(0, performance.now() - this.diagnosticsStarted),
      recordSequence: this.diagnosticSequence,
      stage,
      event,
      sessionId: this.capability?.session_id ?? null,
      turnId: control?.turn_id ?? this.desiredMedia?.turnId ?? null,
      requestId: control?.request_id ?? this.desiredMedia?.requestId ?? null,
      streamEpoch: control?.stream_epoch ?? this.streamEpoch,
      mediaGeneration: control?.media_generation ?? this.desiredMedia?.mediaGeneration ?? null,
      sequence: control?.sequence ?? null,
    }
    if (control !== undefined) record.serverControlType = control.type
    if (control?.type === 'turn.failed') record.failureStage = privacySafeCode(stage, 'controller')
    if (failureCode !== undefined) record.failureCode = privacySafeCode(failureCode, 'unknown_failure')
    if (failure !== undefined) {
      const candidate = failure instanceof Error ? failure.name : typeof failure
      record.failureClass = /^[A-Za-z][A-Za-z0-9]{0,63}$/.test(candidate) ? candidate : 'Error'
    }
    if (control !== undefined) {
      const payload = control.payload
      if (['available', 'unavailable', 'degraded', 'retrying', 'interrupted'].includes(String(payload.user_state))) {
        record.userState = String(payload.user_state)
      }
      if (payload.provider_mode === 'local') record.providerMode = payload.provider_mode
      if (typeof payload.external_transfer === 'boolean') record.externalTransfer = payload.external_transfer
      const integerFields = [
        ['provider_input_unit_count', 'providerInputUnitCount'],
        ['provider_output_unit_count', 'providerOutputUnitCount'],
        ['provider_total_unit_count', 'providerTotalUnitCount'],
      ] as const
      for (const [source, target] of integerFields) {
        const value = payload[source]
        if (Number.isSafeInteger(value) && (value as number) >= 0) record[target] = value as number
      }
      const metricFields = [
        ['endpoint_to_stt_final_ms', 'endpointToSttFinalMs'],
        ['provider_time_to_first_token_ms', 'providerTimeToFirstTokenMs'],
        ['provider_completion_ms', 'providerCompletionMs'],
        ['tts_time_to_first_audio_ms', 'ttsTimeToFirstAudioMs'],
        ['cancellation_latency_ms', 'cancellationLatencyMs'],
        ['total_turn_ms', 'totalTurnMs'],
        ['cpu_utilization_percent', 'cpuUtilizationPercent'],
        ['host_ram_used_mib', 'hostRamUsedMib'],
        ['gpu_vram_used_mib', 'gpuVramUsedMib'],
      ] as const
      for (const [source, target] of metricFields) {
        const value = payload[source]
        if (typeof value === 'number' && Number.isFinite(value) && value >= 0) record[target] = value
      }
    }
    this.diagnostics.push(record)
    if (this.diagnostics.length > 512) this.diagnostics.shift()
    this.callbacks.onDiagnostic?.(record)
  }

  private async failSession(
    message: string,
    code = 'client_failure',
    cause?: unknown,
    preserveMicrophoneError = false,
    notifyConnectionFailure = true,
  ): Promise<void> {
    if (this.stopping) return
    this.recordDiagnostic('client', 'failed', undefined, code, cause ?? message)
    this.stopping = true
    this.startAbort?.abort()
    const cleanup = this.beginResourceRelease(false, preserveMicrophoneError)
    let failure = message
    try {
      await cleanup
    } catch {
      failure = `${message} (не удалось полностью освободить транспорт)`
    } finally {
      if (notifyConnectionFailure) this.callbacks.onConnection('failed', failure)
    }
  }
}
