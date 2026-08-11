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
  private reconnecting = false
  private reconnectRequestPending = false
  private pendingRemoteTrack: RemoteAudioTrack | null = null
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
    this.playoutGeneration += 1
    this.microphone?.stop()
    this.microphone = null
    this.capability = null
    this.controlGate = null
    this.reconnecting = false
    this.reconnectRequestPending = false
    this.pendingRemoteTrack = null
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
    room.on(RoomEvent.TrackSubscribed, (track, _publication, participant) => {
      if (
        track.kind === Track.Kind.Audio &&
        this.capability !== null &&
        participant.identity === `agent-${this.capability.session_id}`
      ) {
        const remoteTrack = track as RemoteAudioTrack
        if (this.reconnecting) {
          this.pendingRemoteTrack = remoteTrack
        } else {
          this.playback.setTrack(remoteTrack)
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
      if (event.type === 'turn.interrupted') this.playback.reset()
      if (event.type === 'session.reconnected') {
        this.streamEpoch = event.stream_epoch
        this.completeReconnect()
      } else if (event.type === 'session.degraded') {
        this.streamEpoch = event.stream_epoch
        this.clearReconnectAckTimer()
        this.reconnecting = false
        this.reconnectRequestPending = false
        this.pendingRemoteTrack = null
      }
      if (event.type === 'turn.playout-ready') void this.publishPlayoutAck(event)
      this.callbacks.onControl(event)
    })
    room.on(RoomEvent.Reconnecting, () => {
      if (this.reconnecting) return
      this.clearReconnectAckTimer()
      this.reconnecting = true
      this.reconnectRequestPending = false
      this.pendingRemoteTrack = null
      this.playoutGeneration += 1
      this.controlGate?.beginReconnect()
      this.playback.suspend()
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
      this.playback.reset()
    } else {
      this.playback.setTrack(track)
    }
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
    this.stopPromise = this.releaseResources(false)
    await this.stopPromise
    this.callbacks.onConnection('failed', message)
  }
}
