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
const PLAYOUT_CONFIRM_TIMEOUT_MS = 2_000

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
  private freshSubscriptionRequired = false
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
      this.armInitialReadyTimeout(capability.expires_in_seconds * 1_000)
      await room.connect(capability.livekit_url, capability.token, { autoSubscribe: true })
      await this.ensureRoomStarting(room)
      const microphone = await createLocalAudioTrack({
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      })
      await this.ensureRoomStarting(room, microphone)
      this.microphone = microphone
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
    if (this.stopPromise !== null) return this.stopPromise
    this.stopping = true
    this.startAbort?.abort()
    this.stopPromise = this.releaseResources()
    return this.stopPromise
  }

  private async releaseResources(notifyClosed = true): Promise<void> {
    this.clearReconnectAckTimer()
    this.clearInitialReadyTimer()
    this.playoutGeneration += 1
    this.microphone?.stop()
    this.microphone = null
    this.capability = null
    this.controlGate = null
    this.reconnecting = false
    this.reconnectRequestPending = false
    this.activeRemoteTrack = null
    this.pendingRemoteTrack = null
    this.remotePublication = null
    this.renewingPublication = null
    this.freshSubscriptionRequired = false
    this.streamEpoch = 0
    this.playback.clear()
    const room = this.room
    this.room = null
    try {
      if (room !== null) await room.disconnect()
    } finally {
      if (notifyClosed) this.callbacks.onConnection('closed')
    }
  }

  private ensureStarting(): void {
    if (this.stopping) throw new Error('voice session start was cancelled')
  }

  private async ensureRoomStarting(
    room: Room,
    microphone?: LocalAudioTrack,
  ): Promise<void> {
    if (!this.stopping) return
    microphone?.stop()
    try {
      await room.disconnect()
    } finally {
      throw new Error('voice session start was cancelled')
    }
  }

  private registerRoomHandlers(room: Room): void {
    room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
      if (
        track.kind === Track.Kind.Audio &&
        this.isExpectedAgent(participant.identity)
      ) {
        const remoteTrack = track as RemoteAudioTrack
        if (this.invalidatedTracks.has(remoteTrack)) return
        this.remotePublication = publication as RemoteTrackPublication
        if (this.freshSubscriptionRequired) {
          this.invalidatedTracks.add(remoteTrack)
          this.beginPublicationRenewal()
          return
        }
        if (this.reconnecting) {
          this.pendingRemoteTrack = remoteTrack
        } else {
          this.activeRemoteTrack = remoteTrack
          this.playback.setTrack(remoteTrack)
        }
      }
    })
    room.on(RoomEvent.TrackUnsubscribed, (track, publication, participant) => {
      if (
        track.kind !== Track.Kind.Audio
        || !this.isExpectedAgent(participant.identity)
        || publication !== this.renewingPublication
      ) return
      this.renewingPublication = null
      this.freshSubscriptionRequired = false
      const remotePublication = publication as RemoteTrackPublication
      remotePublication.setSubscribed(true)
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
      if (event.type === 'turn.interrupted' || event.type === 'turn.failed') {
        this.renewPlaybackTrack()
      }
      let terminalFailure: string | null = null
      if (event.type === 'session.ready') {
        this.clearInitialReadyTimer()
      } else if (event.type === 'session.reconnected') {
        this.streamEpoch = event.stream_epoch
        this.completeReconnect()
      } else if (event.type === 'session.degraded') {
        this.streamEpoch = event.stream_epoch
        this.clearReconnectAckTimer()
        this.reconnecting = false
        this.reconnectRequestPending = false
        this.pendingRemoteTrack = null
        const stage = typeof event.payload.stage === 'string' ? event.payload.stage : 'session'
        const code = typeof event.payload.code === 'string' ? event.payload.code : 'degraded'
        terminalFailure = `Голосовая сессия остановлена (${stage}/${code})`
      }
      if (event.type === 'turn.playout-ready') void this.publishPlayoutAck(event)
      this.callbacks.onControl(event)
      if (terminalFailure !== null) void this.failSession(terminalFailure)
    })
    room.on(RoomEvent.Reconnecting, () => {
      if (this.reconnecting) return
      this.clearReconnectAckTimer()
      this.reconnecting = true
      this.reconnectRequestPending = false
      this.invalidatePlaybackTrack()
      this.playoutGeneration += 1
      this.controlGate?.beginReconnect()
      this.callbacks.onConnection('reconnecting')
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
    if (typeof drainMs !== 'number' || !Number.isFinite(drainMs) || drainMs < 0) {
      await this.failSession('Некорректная граница воспроизведения')
      return
    }
    const generation = ++this.playoutGeneration
    const drained = await this.playback.confirmDrain(drainMs, PLAYOUT_CONFIRM_TIMEOUT_MS)
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

  private completeReconnect(): void {
    this.clearReconnectAckTimer()
    this.reconnecting = false
    this.reconnectRequestPending = false
    const track = this.pendingRemoteTrack
    this.pendingRemoteTrack = null
    if (track === null) {
      this.renewPlaybackTrack()
    } else {
      this.activeRemoteTrack = track
      this.playback.setTrack(track)
    }
  }

  private isExpectedAgent(identity: string): boolean {
    return this.capability !== null && identity === `agent-${this.capability.session_id}`
  }

  private invalidatePlaybackTrack(): void {
    if (this.activeRemoteTrack !== null) this.invalidatedTracks.add(this.activeRemoteTrack)
    if (this.pendingRemoteTrack !== null) this.invalidatedTracks.add(this.pendingRemoteTrack)
    this.activeRemoteTrack = null
    this.pendingRemoteTrack = null
    this.playback.clear()
  }

  private renewPlaybackTrack(): void {
    this.invalidatePlaybackTrack()
    this.freshSubscriptionRequired = true
    this.beginPublicationRenewal()
  }

  private beginPublicationRenewal(): void {
    const publication = this.remotePublication
    if (publication === null || this.renewingPublication !== null) return
    this.renewingPublication = publication
    publication.setSubscribed(false)
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
    const cleanup = this.releaseResources(false)
    this.stopPromise = cleanup.catch(() => undefined)
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
