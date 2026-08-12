import {
  Room,
  RoomEvent,
  Track,
  createLocalAudioTrack,
  type LocalAudioTrack,
  type RemoteAudioTrack,
  type RemoteTrackPublication,
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
const PLAYOUT_ACK_PUBLISH_MARGIN_MS = 250

export interface VoiceClientCallbacks {
  onSession(capability: SessionCapability): void
  onConnection(connection: ConnectionState, error?: string): void
  onControl(event: ControlEvent): void
  onDrop(): void
  onAudioBlocked(blocked: boolean): void
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
  private pendingRemoteTrack: RemoteAudioTrack | null = null
  private remotePublication: RemoteTrackPublication | null = null
  private renewingPublication: RemoteTrackPublication | null = null
  private renewalPhase: 'idle' | 'unsubscribing' | 'subscribing' = 'idle'
  private mediaInvalidationGeneration = 0
  private renewalBoundaryGeneration = 0
  private freshSubscriptionRequired = false
  private pendingMediaGeneration: number | null = null
  private pendingMediaTurnId: string | null = null
  private reconnectMediaGeneration: number | null = null
  private readonly invalidatedTracks = new WeakSet<RemoteAudioTrack>()
  private streamEpoch = 0
  private playoutGeneration = 0

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
      const capability = parseCapability(await response.json())
      this.ensureStarting()
      if (capability === null) throw new Error('invalid room capability response')
      this.capability = capability
      this.streamEpoch = capability.stream_epoch
      this.controlGate = new RealtimeControlGate(capability.session_id, capability.stream_epoch)
      this.callbacks.onSession(capability)

      const room = new Room({ adaptiveStream: false, dynacast: false, disconnectOnPageLeave: true })
      this.room = room
      this.registerRoomHandlers(room)
      this.armInitialReadyTimeout(capability.admission_timeout_ms)
      await room.connect(capability.livekit_url, capability.token, { autoSubscribe: true })
      await this.ensureRoomStarting(room)
      if (!this.hasExpectedAgent(room)) {
        throw new Error('voice session agent is unavailable')
      }
      const microphone = await createLocalAudioTrack({
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      })
      this.microphone = microphone
      await this.ensureRoomStarting(room, microphone)
      await room.localParticipant.publishTrack(microphone, {
        source: Track.Source.Microphone,
        dtx: false,
        red: true,
      })
      await this.ensureRoomStarting(room, microphone)
    } finally {
      if (this.startAbort === abort) this.startAbort = null
    }
  }

  async resumeAudio(): Promise<void> {
    if (this.room === null) return
    await this.room.startAudio()
    this.playback.reset()
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
    this.playoutGeneration += 1
    this.capability = null
    this.controlGate = null
    this.reconnecting = false
    this.reconnectRequestPending = false
    this.activeRemoteTrack = null
    this.pendingRemoteTrack = null
    this.remotePublication = null
    this.renewingPublication = null
    this.renewalPhase = 'idle'
    this.mediaInvalidationGeneration = 0
    this.renewalBoundaryGeneration = 0
    this.freshSubscriptionRequired = false
    this.pendingMediaGeneration = null
    this.pendingMediaTurnId = null
    this.reconnectMediaGeneration = null
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
      this.playback.clear()
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
      if (notifyClosed) {
        this.callbacks.onConnection(
          'failed',
          'Не удалось полностью освободить ресурсы голосовой сессии',
        )
      }
      throw new AggregateError(errors, 'voice session resource cleanup failed')
    }
    if (notifyClosed) this.callbacks.onConnection('closed')
  }

  private ensureStarting(): void {
    if (this.stopping) throw new Error('voice session start was cancelled')
  }

  private async ensureRoomStarting(
    room: Room,
    microphone?: LocalAudioTrack,
  ): Promise<void> {
    if (!this.stopping) return
    if (this.room === null) this.room = room
    if (microphone !== undefined && this.microphone === null) {
      this.microphone = microphone
    }
    await this.stop()
    throw new Error('voice session start was cancelled')
  }

  private registerRoomHandlers(room: Room): void {
    room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
      if (
        track.kind === Track.Kind.Audio &&
        this.isExpectedAgent(participant.identity)
      ) {
        const remoteTrack = track as RemoteAudioTrack
        const remotePublication = publication as RemoteTrackPublication
        this.remotePublication = remotePublication
        if (this.freshSubscriptionRequired) {
          const subscribingPublication = (
            this.renewalPhase === 'subscribing'
            && remotePublication === this.renewingPublication
          )
          const currentRenewal = (
            subscribingPublication
            && this.renewalBoundaryGeneration === this.mediaInvalidationGeneration
          )
          if (currentRenewal && this.invalidatedTracks.has(remoteTrack)) return
          if (currentRenewal) {
            this.renewalPhase = 'idle'
            this.renewingPublication = null
            this.freshSubscriptionRequired = false
          } else {
            this.invalidatedTracks.add(remoteTrack)
            if (subscribingPublication) {
              this.renewalPhase = 'idle'
              this.renewingPublication = null
            }
            this.beginPublicationRenewal()
            return
          }
        } else if (this.invalidatedTracks.has(remoteTrack)) {
          return
        }
        if (this.reconnecting) {
          this.pendingRemoteTrack = remoteTrack
        } else {
          this.activeRemoteTrack = remoteTrack
          try {
            this.playback.setTrack(remoteTrack)
            void this.publishMediaReady()
          } catch {
            void this.failSession('Не удалось безопасно переключить воспроизведение')
          }
        }
      }
    })
    room.on(RoomEvent.TrackUnsubscribed, (track, publication, participant) => {
      if (
        track.kind !== Track.Kind.Audio
        || !this.isExpectedAgent(participant.identity)
        || publication !== this.renewingPublication
        || this.renewalPhase !== 'unsubscribing'
      ) return
      this.renewalPhase = 'subscribing'
      this.renewalBoundaryGeneration = this.mediaInvalidationGeneration
      const remotePublication = publication as RemoteTrackPublication
      try {
        remotePublication.setSubscribed(true)
      } catch {
        void this.failSession('Не удалось безопасно обновить аудиопоток')
      }
    })
    room.on(RoomEvent.DataReceived, (payload, participant, _kind, topic) => {
      if (
        this.capability === null ||
        topic !== CONTROL_TOPIC ||
        participant?.identity !== `agent-${this.capability.session_id}`
      ) return
      const event = parseControlEvent(payload)
      if (event === null || this.controlGate === null || !this.controlGate.accept(event)) {
        this.callbacks.onDrop()
        return
      }
      if (
        event.type === 'turn.interrupted'
        || event.type === 'turn.failed'
        || event.type === 'turn.completed'
        || event.type === 'session.reconnected'
        || event.type === 'session.degraded'
      ) {
        this.playoutGeneration += 1
      }
      let terminalFailure: string | null = null
      if (event.type === 'turn.interrupted' || event.type === 'turn.failed') {
        const mediaGeneration = event.payload.media_generation
        if (
          typeof mediaGeneration !== 'number'
          || !Number.isSafeInteger(mediaGeneration)
          || mediaGeneration < 1
        ) {
          terminalFailure = 'Некорректная граница обновления аудиопотока'
        } else {
          try {
            this.renewPlaybackTrack(event.turn_id, mediaGeneration)
          } catch {
            terminalFailure = 'Не удалось остановить устаревшее воспроизведение'
          }
        }
      }
      if (event.type === 'session.ready') {
        this.clearInitialReadyTimer()
        if (this.reconnectRequestPending) {
          if (
            event.payload.state !== 'ready'
            || event.payload.media_generation !== this.reconnectMediaGeneration
          ) {
            terminalFailure = 'Некорректное подтверждение готовности аудиопотока'
          } else {
            this.clearReconnectAckTimer()
            this.reconnectRequestPending = false
            this.reconnectMediaGeneration = null
          }
        }
      } else if (event.type === 'session.reconnected') {
        this.streamEpoch = event.stream_epoch
        try {
          this.completeReconnect(event)
        } catch {
          terminalFailure = 'Не удалось безопасно восстановить аудиопоток'
        }
      } else if (event.type === 'session.degraded') {
        this.streamEpoch = event.stream_epoch
        this.clearReconnectAckTimer()
        this.reconnecting = false
        this.reconnectRequestPending = false
        this.pendingRemoteTrack = null
        this.reconnectMediaGeneration = null
        const stage = typeof event.payload.stage === 'string' ? event.payload.stage : 'session'
        const code = typeof event.payload.code === 'string' ? event.payload.code : 'degraded'
        terminalFailure = `Голосовая сессия остановлена (${stage}/${code})`
      }
      if (event.type === 'turn.playout-ready') void this.publishPlayoutAck(event)
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
      this.reconnectMediaGeneration = null
      let mediaInvalidationFailed = false
      try {
        this.invalidatePlaybackTrack()
      } catch {
        mediaInvalidationFailed = true
      }
      this.playoutGeneration += 1
      this.controlGate?.beginReconnect()
      this.callbacks.onConnection('reconnecting')
      if (mediaInvalidationFailed) {
        void this.failSession('Не удалось остановить аудио при переподключении')
      }
    })
    room.on(RoomEvent.Reconnected, () => {
      void this.publishReconnect()
    })
    room.on(RoomEvent.Disconnected, () => {
      this.clearReconnectAckTimer()
      this.playoutGeneration += 1
      if (!this.stopping) {
        void this.failSession('Соединение с голосовой сессией потеряно')
      }
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
    try {
      await this.room.localParticipant.publishData(payload, {
        reliable: true,
        topic: CLIENT_CONTROL_TOPIC,
      })
    } catch {
      await this.failSession('Не удалось восстановить сессию')
    }
  }

  private async publishPlayoutAck(event: ControlEvent): Promise<void> {
    const drainMs = event.payload.drain_bound_ms
    const ackTimeoutMs = event.payload.ack_timeout_ms
    if (
      typeof drainMs !== 'number'
      || !Number.isFinite(drainMs)
      || drainMs < 0
      || typeof ackTimeoutMs !== 'number'
      || !Number.isFinite(ackTimeoutMs)
      || ackTimeoutMs <= PLAYOUT_ACK_PUBLISH_MARGIN_MS
      || ackTimeoutMs > 10_000
    ) {
      await this.failSession('Некорректная граница воспроизведения')
      return
    }
    const generation = ++this.playoutGeneration
    let drained = false
    try {
      drained = await this.playback.confirmDrain(
        drainMs,
        ackTimeoutMs - PLAYOUT_ACK_PUBLISH_MARGIN_MS,
      )
    } catch {
      await this.failSession('Не удалось подтвердить воспроизведение ответа')
      return
    }
    if (generation !== this.playoutGeneration || this.stopping) return
    if (!drained || this.room === null || this.capability === null) {
      await this.failSession('Не удалось подтвердить воспроизведение ответа')
      return
    }
    this.clientSequence += 1
    const payload = new TextEncoder().encode(JSON.stringify({
      schema_version: CLIENT_CONTROL_VERSION,
      session_id: this.capability.session_id,
      turn_id: event.turn_id,
      stream_epoch: event.stream_epoch,
      sequence: this.clientSequence,
      type: 'client.playout-completed',
    }))
    try {
      await this.room.localParticipant.publishData(payload, {
        reliable: true,
        topic: CLIENT_CONTROL_TOPIC,
      })
    } catch {
      await this.failSession('Не удалось подтвердить воспроизведение ответа')
    }
  }

  private completeReconnect(event: ControlEvent): void {
    const mediaGeneration = event.payload.media_generation
    const mediaReadyTimeoutMs = event.payload.media_ready_timeout_ms
    if (
      event.payload.state !== 'awaiting_media'
      || typeof mediaGeneration !== 'number'
      || !Number.isSafeInteger(mediaGeneration)
      || mediaGeneration < 1
      || typeof mediaReadyTimeoutMs !== 'number'
      || !Number.isSafeInteger(mediaReadyTimeoutMs)
      || mediaReadyTimeoutMs < 250
      || mediaReadyTimeoutMs > RECONNECT_ACK_TIMEOUT_MS
    ) throw new Error('invalid reconnect media generation')
    this.clearReconnectAckTimer()
    this.reconnectAckTimer = setTimeout(() => {
      void this.failSession('Сервер не подтвердил готовность аудиопотока')
    }, mediaReadyTimeoutMs)
    this.reconnecting = false
    this.reconnectMediaGeneration = mediaGeneration
    this.pendingMediaTurnId = 'session'
    this.pendingMediaGeneration = mediaGeneration
    const track = this.pendingRemoteTrack
    this.pendingRemoteTrack = null
    if (track === null || this.invalidatedTracks.has(track)) {
      this.renewPlaybackTrack('session', mediaGeneration)
    } else {
      this.activeRemoteTrack = track
      this.playback.setTrack(track)
      void this.publishMediaReady()
    }
  }

  private isExpectedAgent(identity: string): boolean {
    return this.capability !== null && identity === `agent-${this.capability.session_id}`
  }

  private hasExpectedAgent(room: Room): boolean {
    if (this.capability === null) return false
    return room.remoteParticipants.has(`agent-${this.capability.session_id}`)
  }

  private invalidatePlaybackTrack(): void {
    if (this.activeRemoteTrack !== null) this.invalidatedTracks.add(this.activeRemoteTrack)
    if (this.pendingRemoteTrack !== null) this.invalidatedTracks.add(this.pendingRemoteTrack)
    this.activeRemoteTrack = null
    this.pendingRemoteTrack = null
    this.playback.clear()
  }

  private renewPlaybackTrack(turnId?: string, mediaGeneration?: number): void {
    this.invalidatePlaybackTrack()
    this.mediaInvalidationGeneration += 1
    this.freshSubscriptionRequired = true
    if (turnId !== undefined && mediaGeneration !== undefined) {
      this.pendingMediaTurnId = turnId
      this.pendingMediaGeneration = mediaGeneration
    }
    this.beginPublicationRenewal()
  }

  private async publishMediaReady(): Promise<void> {
    const room = this.room
    const capability = this.capability
    const mediaGeneration = this.pendingMediaGeneration
    const turnId = this.pendingMediaTurnId
    if (
      room === null
      || capability === null
      || mediaGeneration === null
      || turnId === null
      || this.freshSubscriptionRequired
      || this.reconnecting
      || this.stopping
    ) return
    const streamEpoch = this.streamEpoch
    this.clientSequence += 1
    const payload = new TextEncoder().encode(JSON.stringify({
      schema_version: CLIENT_CONTROL_VERSION,
      session_id: capability.session_id,
      turn_id: turnId,
      stream_epoch: streamEpoch,
      sequence: this.clientSequence,
      media_generation: mediaGeneration,
      type: 'client.media-ready',
    }))
    try {
      await room.localParticipant.publishData(payload, {
        reliable: true,
        topic: CLIENT_CONTROL_TOPIC,
      })
      if (
        this.pendingMediaGeneration === mediaGeneration
        && this.pendingMediaTurnId === turnId
        && this.streamEpoch === streamEpoch
        && !this.reconnecting
      ) {
        this.pendingMediaGeneration = null
        this.pendingMediaTurnId = null
      }
    } catch {
      await this.failSession('Не удалось подтвердить готовность аудиопотока')
    }
  }

  private beginPublicationRenewal(): void {
    const publication = this.remotePublication
    if (publication === null || this.renewalPhase !== 'idle') return
    this.renewingPublication = publication
    this.renewalPhase = 'unsubscribing'
    try {
      publication.setSubscribed(false)
    } catch {
      void this.failSession('Не удалось безопасно обновить аудиопоток')
    }
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

  private async failSession(message: string): Promise<void> {
    if (this.stopping) return
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
