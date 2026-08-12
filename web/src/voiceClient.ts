import {
  Room,
  RoomEvent,
  Track,
  createLocalAudioTrack,
  type LocalAudioTrack,
  type RemoteAudioTrack,
} from 'livekit-client'
import { AudioPlaybackBoundary } from './playback'
import {
  CLIENT_CONTROL_TOPIC,
  CLIENT_CONTROL_VERSION,
  CONTROL_TOPIC,
  RealtimeControlGate,
  parseCapability,
  parseControlEvent,
  type ConnectionState,
  type ControlEvent,
  type SessionCapability,
} from './state'

const RECONNECT_ACK_TIMEOUT_MS = 5_000

export interface VoiceDiagnosticRecord {
  timestamp: string
  stage: string
  event: string
  sessionId: string | null
  turnId: string | null
  streamEpoch: number
  sequence: number | null
  serverControlType?: string
  failureStage?: string
  failureCode?: string
  failureMessage?: string
}

export interface VoiceClientCallbacks {
  onSession(capability: SessionCapability): void
  onConnection(connection: ConnectionState, error?: string): void
  onControl(event: ControlEvent): void
  onDrop(): void
  onAudioBlocked(blocked: boolean): void
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
  private initialReadyTimer: ReturnType<typeof setTimeout> | null = null
  private reconnecting = false
  private reconnectRequestPending = false
  private activeRemoteTrack: RemoteAudioTrack | null = null
  private streamEpoch = 0
  private readonly diagnostics: VoiceDiagnosticRecord[] = []

  constructor(
    audioContainer: HTMLElement,
    private readonly callbacks: VoiceClientCallbacks,
  ) {
    this.playback = new AudioPlaybackBoundary(audioContainer, callbacks.onAudioBlocked)
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
      const microphone = await createLocalAudioTrack({
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      })
      this.microphone = microphone
      await this.ensureRoomStarting(room, microphone)
      startupStage = 'microphone_publication'
      await room.localParticipant.publishTrack(microphone, {
        source: Track.Source.Microphone,
        dtx: false,
        red: true,
      })
      await this.ensureRoomStarting(room, microphone)
    } catch (error) {
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

  resetPlayback(): void {
    this.playback.reset()
  }

  async stop(): Promise<void> {
    this.stopping = true
    this.startAbort?.abort()
    return this.beginResourceRelease(true)
  }

  private beginResourceRelease(notifyClosed: boolean): Promise<void> {
    if (this.stopPromise !== null) return this.stopPromise
    const cleanup = this.releaseResources(notifyClosed)
    const tracked = cleanup.finally(() => {
      if (this.stopPromise === tracked) this.stopPromise = null
    })
    this.stopPromise = tracked
    return tracked
  }

  private async releaseResources(notifyClosed: boolean): Promise<void> {
    this.clearReconnectAckTimer()
    this.clearInitialReadyTimer()
    this.capability = null
    this.controlGate = null
    this.reconnecting = false
    this.reconnectRequestPending = false
    this.activeRemoteTrack = null
    this.streamEpoch = 0
    const errors: unknown[] = []
    const microphone = this.microphone
    if (microphone !== null) {
      try {
        microphone.stop()
        if (this.microphone === microphone) this.microphone = null
      } catch (error) {
        errors.push(error)
      }
    }
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

  private async ensureRoomStarting(room: Room, microphone?: LocalAudioTrack): Promise<void> {
    if (!this.stopping) return
    if (this.room === null) this.room = room
    if (microphone !== undefined && this.microphone === null) this.microphone = microphone
    await this.stop()
    throw new Error('voice session start was cancelled')
  }

  private registerRoomHandlers(room: Room): void {
    room.on(RoomEvent.TrackSubscribed, (track, _publication, participant) => {
      if (track.kind !== Track.Kind.Audio || !this.isExpectedAgent(participant.identity)) return
      const remoteTrack = track as RemoteAudioTrack
      if (this.activeRemoteTrack === remoteTrack) return
      this.activeRemoteTrack = remoteTrack
      this.playback.setTrack(remoteTrack)
      this.recordDiagnostic('playback', 'persistent_track_attached')
    })
    room.on(RoomEvent.TrackUnsubscribed, (track, _publication, participant) => {
      if (
        track.kind !== Track.Kind.Audio
        || !this.isExpectedAgent(participant.identity)
        || this.activeRemoteTrack !== track
      ) return
      this.activeRemoteTrack = null
      this.playback.clear()
      this.recordDiagnostic('playback', 'persistent_track_detached')
      if (!this.reconnecting && !this.stopping) {
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
        this.callbacks.onDrop()
        return
      }
      let terminalFailure: string | null = null
      if (event.type === 'session.ready') {
        this.clearInitialReadyTimer()
        this.clearReconnectAckTimer()
        this.reconnectRequestPending = false
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
      const serverStage = serverFailure && typeof event.payload.stage === 'string'
        ? event.payload.stage
        : event.type === 'session.degraded' ? 'session' : event.type === 'turn.failed' ? 'controller' : 'control'
      const serverCode = serverFailure && typeof event.payload.code === 'string'
        ? event.payload.code
        : event.type === 'session.degraded' ? 'degraded' : event.type === 'turn.failed' ? 'unknown_failure' : undefined
      this.recordDiagnostic(serverStage, event.type, event, serverCode)
      try {
        this.callbacks.onControl(event)
      } catch {
        terminalFailure ??= 'Не удалось применить состояние голосовой сессии'
      }
      if (terminalFailure !== null) void this.failSession(terminalFailure)
    })
    room.on(RoomEvent.Reconnecting, () => {
      if (this.reconnecting) return
      this.clearReconnectAckTimer()
      this.reconnecting = true
      this.reconnectRequestPending = false
      try {
        this.playback.suspend()
      } catch (error) {
        this.recordDiagnostic('playback', 'suspend_failed', undefined, 'playback_suspend_failed', error)
      }
      this.controlGate?.beginReconnect()
      this.callbacks.onConnection('reconnecting')
    })
    room.on(RoomEvent.Reconnected, () => {
      if (this.activeRemoteTrack !== null) this.playback.reset()
      void this.publishReconnect()
    })
    room.on(RoomEvent.Disconnected, () => {
      this.clearReconnectAckTimer()
      if (!this.stopping) void this.failSession('Соединение с голосовой сессией потеряно')
    })
    room.on(RoomEvent.ParticipantDisconnected, (participant) => {
      if (this.isExpectedAgent(participant.identity)) {
        void this.failSession('Агент голосовой сессии отключился')
      }
    })
    room.on(RoomEvent.MediaDevicesError, () => {
      void this.failSession('Микрофон недоступен')
    })
  }

  private async publishReconnect(): Promise<void> {
    if (
      this.room === null
      || this.capability === null
      || !this.reconnecting
      || this.reconnectRequestPending
    ) return
    this.reconnectRequestPending = true
    this.clientSequence += 1
    const payload = new TextEncoder().encode(JSON.stringify({
      schema_version: CLIENT_CONTROL_VERSION,
      session_id: this.capability.session_id,
      stream_epoch: this.streamEpoch,
      sequence: this.clientSequence,
      type: 'client.reconnected',
    }))
    this.clearReconnectAckTimer()
    this.reconnectAckTimer = setTimeout(() => {
      void this.failSession('Сервер не подтвердил восстановление сессии')
    }, RECONNECT_ACK_TIMEOUT_MS)
    const room = this.room
    const streamEpoch = this.streamEpoch
    try {
      await room.localParticipant.publishData(payload, {
        reliable: true,
        topic: CLIENT_CONTROL_TOPIC,
      })
    } catch (error) {
      if (!this.isSuperseded(room, streamEpoch)) {
        await this.failSession('Не удалось восстановить сессию', 'reconnect_publish_failed', error)
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

  private clearReconnectAckTimer(): void {
    if (this.reconnectAckTimer === null) return
    clearTimeout(this.reconnectAckTimer)
    this.reconnectAckTimer = null
  }

  private isSuperseded(room: Room, streamEpoch: number): boolean {
    return this.room !== room || this.streamEpoch !== streamEpoch || this.stopping
  }

  private recordDiagnostic(
    stage: string,
    event: string,
    control?: ControlEvent,
    failureCode?: string,
    failure?: unknown,
  ): void {
    const record: VoiceDiagnosticRecord = {
      timestamp: new Date().toISOString(),
      stage,
      event,
      sessionId: this.capability?.session_id ?? null,
      turnId: control?.turn_id ?? null,
      streamEpoch: control?.stream_epoch ?? this.streamEpoch,
      sequence: control?.sequence ?? null,
    }
    if (control !== undefined) record.serverControlType = control.type
    if (control?.type === 'turn.failed') record.failureStage = stage
    if (failureCode !== undefined) record.failureCode = failureCode
    if (failure !== undefined) {
      record.failureMessage = failure instanceof Error
        ? `${failure.name}: ${failure.message}`.slice(0, 512)
        : String(failure).slice(0, 512)
    }
    this.diagnostics.push(record)
    if (this.diagnostics.length > 512) this.diagnostics.shift()
    this.callbacks.onDiagnostic?.(record)
  }

  private async failSession(message: string, code = 'client_failure', cause?: unknown): Promise<void> {
    if (this.stopping) return
    this.recordDiagnostic('client', 'failed', undefined, code, cause ?? message)
    this.stopping = true
    this.startAbort?.abort()
    const cleanup = this.beginResourceRelease(false)
    let failure = message
    try {
      await cleanup
    } catch {
      failure = `${message} (не удалось полностью освободить транспорт)`
    } finally {
      this.callbacks.onConnection('failed', failure)
    }
  }
}
