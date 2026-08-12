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
  OUTPUT_MEDIA_MAX_SAMPLES,
  RealtimeControlGate,
  parseCapability,
  parseControlEvent,
  type ConnectionState,
  type ControlEvent,
  type SessionCapability,
} from './state'

const RECONNECT_ACK_TIMEOUT_MS = 5_000
const MAX_TRACK_GENERATIONS = 128

interface PendingPlayoutBoundary {
  turnId: string
  streamEpoch: number
  mediaGeneration: number
  completedPublicationId: string
  finalSampleCount: number
  sampleRateHz: number
  stage: 'waiting-drain' | 'drain-sending' | 'drain-sent' | 'waiting-retirement' | 'completion-sent'
  waitStarted: boolean
  retirementObserved: boolean
  retiredControlObserved: boolean
}

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
  private playoutAckTimer: ReturnType<typeof setTimeout> | null = null
  private initialReadyTimer: ReturnType<typeof setTimeout> | null = null
  private reconnecting = false
  private reconnectRequestPending = false
  private activeRemoteTrack: RemoteAudioTrack | null = null
  private pendingRemoteTrack: RemoteAudioTrack | null = null
  private pendingMediaGeneration: number | null = null
  private pendingMediaTurnId: string | null = null
  private expectedMediaPublicationId: string | null = null
  private reconnectMediaGeneration: number | null = null
  private readonly publicationTracks = new Map<string, RemoteAudioTrack>()
  private pendingPlayoutBoundary: PendingPlayoutBoundary | null = null
  private pendingWaitStartedGeneration: number | null = null
  private mediaReadyPublishingGeneration: number | null = null
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
    this.clearPlayoutAckTimer()
    this.clearInitialReadyTimer()
    this.playoutGeneration += 1
    this.capability = null
    this.controlGate = null
    this.reconnecting = false
    this.reconnectRequestPending = false
    this.activeRemoteTrack = null
    this.pendingRemoteTrack = null
    this.pendingMediaGeneration = null
    this.pendingMediaTurnId = null
    this.expectedMediaPublicationId = null
    this.reconnectMediaGeneration = null
    this.publicationTracks.clear()
    this.pendingPlayoutBoundary = null
    this.pendingWaitStartedGeneration = null
    this.mediaReadyPublishingGeneration = null
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
        const publicationId = this.publicationId(remotePublication)
        if (publicationId !== null) {
          this.publicationTracks.set(publicationId, remoteTrack)
          if (this.publicationTracks.size > MAX_TRACK_GENERATIONS) {
            void this.failSession('Превышена граница обновления аудиопотока')
            return
          }
        }
        if (
          this.pendingMediaGeneration !== null
          && publicationId !== null
          && publicationId === this.expectedMediaPublicationId
        ) {
          this.activateCorrelatedTrack(remoteTrack)
          void this.publishMediaReady()
          return
        }
        if (this.reconnecting) this.pendingRemoteTrack = remoteTrack
      }
    })
    room.on(RoomEvent.TrackUnsubscribed, (track, publication, participant) => {
      if (
        track.kind !== Track.Kind.Audio
        || !this.isExpectedAgent(participant.identity)
      ) return
      const publicationId = this.publicationId(publication as RemoteTrackPublication)
      if (publicationId === null) return
      const registeredTrack = this.publicationTracks.get(publicationId)
      if (registeredTrack !== track) return
      this.publicationTracks.delete(publicationId)
      const boundary = this.pendingPlayoutBoundary
      if (
        boundary !== null
        && boundary.completedPublicationId === publicationId
      ) {
        boundary.retirementObserved = true
        if (boundary.stage === 'waiting-retirement') {
          void this.completeRetiredBoundary(boundary)
        }
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
        this.clearPlayoutAckTimer()
        this.pendingPlayoutBoundary = null
        const mediaGeneration = event.payload.media_generation
        const publicationId = event.payload.media_publication_id
        if (
          typeof mediaGeneration !== 'number'
          || !Number.isSafeInteger(mediaGeneration)
          || mediaGeneration < 1
          || (publicationId !== undefined && (
            typeof publicationId !== 'string'
            || publicationId.length < 1
            || publicationId.length > 128
          ))
        ) {
          terminalFailure = 'Некорректная граница обновления аудиопотока'
        } else {
          try {
            this.invalidatePlaybackTrack()
            if (publicationId !== undefined) {
              this.publicationTracks.delete(publicationId)
            }
            this.pendingMediaTurnId = event.turn_id
            this.pendingMediaGeneration = mediaGeneration
            this.expectedMediaPublicationId = null
            void this.beginWait(
              event.turn_id, mediaGeneration, 'media-ready', 2_750,
            ).then(() => this.publishMediaReady())
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
        this.clearPlayoutAckTimer()
        this.pendingPlayoutBoundary = null
        this.reconnecting = false
        this.reconnectRequestPending = false
        this.pendingRemoteTrack = null
        this.reconnectMediaGeneration = null
        const stage = typeof event.payload.stage === 'string' ? event.payload.stage : 'session'
        const code = typeof event.payload.code === 'string' ? event.payload.code : 'degraded'
        terminalFailure = `Голосовая сессия остановлена (${stage}/${code})`
      }
      if (event.type === 'turn.speaking') {
        try {
          this.beginResponseBoundary(event)
        } catch {
          terminalFailure = 'Некорректная граница начала воспроизведения'
        }
      } else if (event.type === 'turn.playout-ready') {
        try {
          this.beginPlayoutBoundary(event)
        } catch {
          terminalFailure = 'Некорректная граница воспроизведения'
        }
      } else if (event.type === 'turn.playout-retired') {
        try {
          this.beginRetiredBoundary(event)
        } catch {
          terminalFailure = 'Некорректная граница завершения аудиопотока'
        }
      }
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
      this.clearPlayoutAckTimer()
      this.pendingPlayoutBoundary = null
      this.reconnecting = true
      this.reconnectRequestPending = false
      this.reconnectMediaGeneration = null
      this.pendingWaitStartedGeneration = null
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

  private beginResponseBoundary(event: ControlEvent): void {
    const mediaGeneration = event.payload.media_generation
    const publicationId = event.payload.media_publication_id
    const timeoutMs = event.payload.media_ready_timeout_ms
    const ackDeadlineMs = event.payload.media_ready_ack_deadline_ms
    const track = typeof publicationId === 'string'
      ? this.publicationTracks.get(publicationId)
      : undefined
    if (
      event.payload.state !== 'awaiting_media'
      || typeof mediaGeneration !== 'number'
      || !Number.isSafeInteger(mediaGeneration)
      || mediaGeneration < 1
      || typeof publicationId !== 'string'
      || publicationId.length < 1
      || publicationId.length > 128
      || typeof timeoutMs !== 'number'
      || !Number.isSafeInteger(timeoutMs)
      || typeof ackDeadlineMs !== 'number'
      || !Number.isSafeInteger(ackDeadlineMs)
      || ackDeadlineMs < 1
      || ackDeadlineMs >= timeoutMs
    ) throw new Error('invalid response media boundary')
    this.pendingMediaTurnId = event.turn_id
    this.pendingMediaGeneration = mediaGeneration
    this.expectedMediaPublicationId = publicationId
    void this.beginWait(
      event.turn_id, mediaGeneration, 'media-ready', ackDeadlineMs,
    ).then(() => this.publishMediaReady())
    if (track !== undefined) {
      this.activateCorrelatedTrack(track)
      void this.publishMediaReady()
    }
  }

  private beginPlayoutBoundary(event: ControlEvent): void {
    const ackTimeoutMs = event.payload.ack_timeout_ms
    const ackDeadlineMs = event.payload.ack_deadline_ms
    const mediaGeneration = event.payload.media_generation
    const completedPublicationId = event.payload.completed_publication_id
    const finalSampleCount = event.payload.final_sample_count
    const sampleRateHz = event.payload.sample_rate_hz
    if (
      event.payload.state !== 'awaiting_client_playout_boundary'
      || typeof ackTimeoutMs !== 'number'
      || !Number.isSafeInteger(ackTimeoutMs)
      || typeof ackDeadlineMs !== 'number'
      || !Number.isSafeInteger(ackDeadlineMs)
      || ackDeadlineMs < 1
      || ackDeadlineMs >= ackTimeoutMs
      || ackTimeoutMs > 10_000
      || typeof mediaGeneration !== 'number'
      || !Number.isSafeInteger(mediaGeneration)
      || mediaGeneration < 1
      || typeof completedPublicationId !== 'string'
      || completedPublicationId.length < 1
      || completedPublicationId.length > 128
      || typeof finalSampleCount !== 'number'
      || !Number.isSafeInteger(finalSampleCount)
      || finalSampleCount < 1
      || finalSampleCount > OUTPUT_MEDIA_MAX_SAMPLES
      || typeof sampleRateHz !== 'number'
      || !Number.isSafeInteger(sampleRateHz)
      || sampleRateHz !== 16_000
    ) throw new Error('invalid playout boundary')
    this.pendingPlayoutBoundary = {
      turnId: event.turn_id,
      streamEpoch: event.stream_epoch,
      mediaGeneration,
      completedPublicationId,
      finalSampleCount,
      sampleRateHz,
      stage: 'waiting-drain',
      waitStarted: false,
      retirementObserved: false,
      retiredControlObserved: false,
    }
    this.clearPlayoutAckTimer()
    void this.beginWait(
      event.turn_id, mediaGeneration, 'playout', ackDeadlineMs,
    ).then(() => {
      const boundary = this.pendingPlayoutBoundary
      if (
        boundary !== null
        && boundary.turnId === event.turn_id
        && boundary.mediaGeneration === mediaGeneration
      ) {
        boundary.waitStarted = true
        void this.completePlayoutBoundary()
      }
    })
  }

  private async completePlayoutBoundary(): Promise<void> {
    const boundary = this.pendingPlayoutBoundary
    const room = this.room
    const capability = this.capability
    if (
      boundary === null
      || room === null
      || capability === null
      || boundary.stage !== 'waiting-drain'
      || !boundary.waitStarted
      || this.stopping
    ) return
    const streamEpoch = this.streamEpoch
    const turnId = boundary.turnId
    const mediaGeneration = boundary.mediaGeneration
    const publicationId = boundary.completedPublicationId
    const completedTrack = this.publicationTracks.get(publicationId)
    if (
      completedTrack === undefined
      || completedTrack !== this.activeRemoteTrack
      || completedTrack.mediaStreamTrack?.readyState !== 'live'
      || boundary.retirementObserved
      || boundary.streamEpoch !== streamEpoch
    ) return
    try {
      await this.playback.waitForFinitePlayout(
        boundary.finalSampleCount,
        boundary.sampleRateHz,
      )
    } catch {
      if (this.pendingPlayoutBoundary === boundary) {
        await this.failSession('Не удалось подтвердить границу аудиопотока')
      }
      return
    }
    if (
      this.pendingPlayoutBoundary !== boundary
      || this.room !== room
      || this.capability !== capability
      || this.streamEpoch !== streamEpoch
      || boundary.streamEpoch !== streamEpoch
      || boundary.turnId !== turnId
      || boundary.mediaGeneration !== mediaGeneration
      || boundary.completedPublicationId !== publicationId
      || boundary.stage !== 'waiting-drain'
      || boundary.retirementObserved
      || this.publicationTracks.get(publicationId) !== completedTrack
      || this.activeRemoteTrack !== completedTrack
      || completedTrack.mediaStreamTrack?.readyState !== 'live'
      || this.stopping
    ) return
    this.clientSequence += 1
    const payload = new TextEncoder().encode(JSON.stringify({
      schema_version: CLIENT_CONTROL_VERSION,
      session_id: capability.session_id,
      turn_id: turnId,
      stream_epoch: streamEpoch,
      sequence: this.clientSequence,
      media_generation: mediaGeneration,
      completed_publication_id: publicationId,
      type: 'client.playout-drained',
    }))
    boundary.stage = 'drain-sending'
    try {
      await room.localParticipant.publishData(payload, {
        reliable: true,
        topic: CLIENT_CONTROL_TOPIC,
      })
      if (
        this.pendingPlayoutBoundary !== boundary
        || this.room !== room
        || this.capability !== capability
        || this.streamEpoch !== streamEpoch
        || boundary.streamEpoch !== streamEpoch
        || boundary.turnId !== turnId
        || boundary.mediaGeneration !== mediaGeneration
        || boundary.completedPublicationId !== publicationId
        || boundary.stage !== 'drain-sending'
        || this.stopping
      ) return
      boundary.stage = 'drain-sent'
      if (boundary.retiredControlObserved) {
        boundary.stage = 'waiting-retirement'
        void this.completeRetiredBoundary(boundary)
      }
    } catch {
      if (this.pendingPlayoutBoundary === boundary) {
        await this.failSession('Не удалось подтвердить границу аудиопотока')
      }
    }
  }

  private beginRetiredBoundary(event: ControlEvent): void {
    const boundary = this.pendingPlayoutBoundary
    if (
      boundary === null
      || !['drain-sending', 'drain-sent'].includes(boundary.stage)
      || event.payload.state !== 'awaiting_publication_unsubscribed'
      || event.turn_id !== boundary.turnId
      || event.stream_epoch !== boundary.streamEpoch
      || event.payload.media_generation !== boundary.mediaGeneration
      || event.payload.completed_publication_id !== boundary.completedPublicationId
    ) throw new Error('invalid retired playout boundary')
    boundary.retiredControlObserved = true
    if (boundary.stage === 'drain-sent') {
      boundary.stage = 'waiting-retirement'
      void this.completeRetiredBoundary(boundary)
    }
  }

  private async completeRetiredBoundary(boundary: PendingPlayoutBoundary): Promise<void> {
    if (
      this.pendingPlayoutBoundary !== boundary
      || boundary.stage !== 'waiting-retirement'
      || !boundary.retirementObserved
      || this.publicationTracks.has(boundary.completedPublicationId)
      || this.stopping
    ) return
    boundary.stage = 'completion-sent'
    try {
      const room = this.room
      const capability = this.capability
      if (room === null || capability === null) return
      this.clientSequence += 1
      const payload = new TextEncoder().encode(JSON.stringify({
        schema_version: CLIENT_CONTROL_VERSION,
        session_id: capability.session_id,
        turn_id: boundary.turnId,
        stream_epoch: boundary.streamEpoch,
        sequence: this.clientSequence,
        media_generation: boundary.mediaGeneration,
        completed_publication_id: boundary.completedPublicationId,
        type: 'client.playout-completed',
      }))
      await room.localParticipant.publishData(payload, {
        reliable: true,
        topic: CLIENT_CONTROL_TOPIC,
      })
      if (this.pendingPlayoutBoundary === boundary) {
        this.pendingPlayoutBoundary = null
        this.clearPlayoutAckTimer()
        this.invalidatePlaybackTrack()
        this.publicationTracks.delete(boundary.completedPublicationId)
      }
    } catch {
      if (this.pendingPlayoutBoundary === boundary) {
        await this.failSession('Не удалось подтвердить завершение аудиопотока')
      }
    }
  }

  private completeReconnect(event: ControlEvent): void {
    const mediaGeneration = event.payload.media_generation
    const mediaReadyTimeoutMs = event.payload.media_ready_timeout_ms
    const mediaReadyAckDeadlineMs = event.payload.media_ready_ack_deadline_ms
    const publicationId = event.payload.media_publication_id
    if (
      event.payload.state !== 'awaiting_media'
      || typeof mediaGeneration !== 'number'
      || !Number.isSafeInteger(mediaGeneration)
      || mediaGeneration < 1
      || typeof mediaReadyTimeoutMs !== 'number'
      || !Number.isSafeInteger(mediaReadyTimeoutMs)
      || mediaReadyTimeoutMs < 250
      || mediaReadyTimeoutMs > RECONNECT_ACK_TIMEOUT_MS
      || typeof mediaReadyAckDeadlineMs !== 'number'
      || !Number.isSafeInteger(mediaReadyAckDeadlineMs)
      || mediaReadyAckDeadlineMs < 1
      || mediaReadyAckDeadlineMs >= mediaReadyTimeoutMs
      || typeof publicationId !== 'string'
      || publicationId.length < 1
      || publicationId.length > 128
    ) throw new Error('invalid reconnect media generation')
    this.clearReconnectAckTimer()
    this.reconnecting = false
    this.reconnectMediaGeneration = mediaGeneration
    this.pendingRemoteTrack = null
    this.pendingMediaTurnId = 'session'
    this.pendingMediaGeneration = mediaGeneration
    this.expectedMediaPublicationId = publicationId
    void this.beginWait(
      'session', mediaGeneration, 'media-ready', mediaReadyAckDeadlineMs,
    ).then(() => this.publishMediaReady())
    this.selectMediaPublication('session', mediaGeneration, publicationId)
  }

  private isExpectedAgent(identity: string): boolean {
    return this.capability !== null && identity === `agent-${this.capability.session_id}`
  }

  private hasExpectedAgent(room: Room): boolean {
    if (this.capability === null) return false
    return room.remoteParticipants.has(`agent-${this.capability.session_id}`)
  }

  private publicationId(publication: RemoteTrackPublication): string | null {
    const publicationId = publication.trackSid
    return typeof publicationId === 'string' && publicationId.length > 0
      ? publicationId
      : null
  }

  private activateCorrelatedTrack(track: RemoteAudioTrack): void {
    if (this.activeRemoteTrack === track) return
    this.invalidatePlaybackTrack()
    this.activeRemoteTrack = track
    this.playback.setTrack(track)
  }

  private selectMediaPublication(
    turnId: string,
    mediaGeneration: number,
    publicationId: string,
  ): void {
    this.invalidatePlaybackTrack()
    this.pendingMediaTurnId = turnId
    this.pendingMediaGeneration = mediaGeneration
    this.expectedMediaPublicationId = publicationId
    const track = this.publicationTracks.get(publicationId)
    if (track !== undefined) {
      this.activateCorrelatedTrack(track)
      void this.publishMediaReady()
    }
  }

  private invalidatePlaybackTrack(): void {
    this.activeRemoteTrack = null
    this.pendingRemoteTrack = null
    this.playback.clear()
  }

  private async beginWait(
    turnId: string,
    mediaGeneration: number,
    waitKind: 'media-ready' | 'playout',
    clientDeadlineMs: number,
  ): Promise<void> {
    const room = this.room
    const capability = this.capability
    if (room === null || capability === null || this.stopping) return
    this.clearPlayoutAckTimer()
    this.playoutAckTimer = setTimeout(() => {
      void this.failSession(
        waitKind === 'playout'
          ? 'Не удалось подтвердить границу аудиопотока'
          : 'Сервер не подтвердил готовность аудиопотока',
      )
    }, clientDeadlineMs)
    const streamEpoch = this.streamEpoch
    this.clientSequence += 1
    const payload = new TextEncoder().encode(JSON.stringify({
      schema_version: CLIENT_CONTROL_VERSION,
      session_id: capability.session_id,
      turn_id: turnId,
      stream_epoch: streamEpoch,
      sequence: this.clientSequence,
      media_generation: mediaGeneration,
      wait_kind: waitKind,
      type: 'client.wait-started',
    }))
    try {
      await room.localParticipant.publishData(payload, {
        reliable: true,
        topic: CLIENT_CONTROL_TOPIC,
      })
      if (
        this.streamEpoch === streamEpoch
        && !this.stopping
        && (
          this.pendingMediaGeneration === mediaGeneration
          || this.pendingPlayoutBoundary?.mediaGeneration === mediaGeneration
        )
      ) {
        this.pendingWaitStartedGeneration = mediaGeneration
      }
    } catch {
      await this.failSession('Не удалось запустить подтверждение аудиопотока')
    }
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
      || this.reconnecting
      || this.stopping
      || this.pendingWaitStartedGeneration !== mediaGeneration
      || this.mediaReadyPublishingGeneration === mediaGeneration
      || (
        this.expectedMediaPublicationId !== null
        && this.publicationTracks.get(this.expectedMediaPublicationId)
          !== this.activeRemoteTrack
      )
    ) return
    this.mediaReadyPublishingGeneration = mediaGeneration
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
      if (this.expectedMediaPublicationId !== null) {
        await this.playback.prepareFinitePlayout()
      }
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
        this.pendingWaitStartedGeneration = null
        this.expectedMediaPublicationId = null
        this.clearPlayoutAckTimer()
        if (turnId === 'session') {
          this.clearReconnectAckTimer()
          this.reconnectAckTimer = setTimeout(() => {
            void this.failSession('Сервер не подтвердил готовность аудиопотока')
          }, RECONNECT_ACK_TIMEOUT_MS)
        }
      }
    } catch {
      await this.failSession('Не удалось подтвердить готовность аудиопотока')
    } finally {
      if (this.mediaReadyPublishingGeneration === mediaGeneration) {
        this.mediaReadyPublishingGeneration = null
      }
    }
  }

  private armInitialReadyTimeout(timeoutMs: number): void {
    this.clearInitialReadyTimer()
    this.initialReadyTimer = setTimeout(() => {
      void this.failSession('Сервер не подтвердил готовность голосовой сессии')
    }, timeoutMs)
  }

  private clearPlayoutAckTimer(): void {
    if (this.playoutAckTimer === null) return
    clearTimeout(this.playoutAckTimer)
    this.playoutAckTimer = null
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
