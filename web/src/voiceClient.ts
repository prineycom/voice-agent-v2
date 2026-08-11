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

  private async releaseResources(): Promise<void> {
    this.microphone?.stop()
    this.microphone = null
    this.capability = null
    this.controlGate = null
    this.playback.clear()
    const room = this.room
    this.room = null
    try {
      if (room !== null) await room.disconnect()
    } finally {
      this.callbacks.onConnection('closed')
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
        this.playback.setTrack(track as RemoteAudioTrack)
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
      if (event.type === 'turn.interrupted' || event.type === 'session.reconnected') {
        this.playback.reset()
      }
      this.callbacks.onControl(event)
    })
    room.on(RoomEvent.Reconnecting, () => {
      this.controlGate?.beginReconnect()
      this.playback.suspend()
      this.callbacks.onConnection('reconnecting')
    })
    room.on(RoomEvent.Reconnected, () => {
      void this.publishReconnect()
    })
    room.on(RoomEvent.Disconnected, () => {
      if (!this.stopping) {
        this.playback.clear()
        this.callbacks.onConnection('closed')
      }
    })
    room.on(RoomEvent.MediaDevicesError, () => {
      this.callbacks.onConnection('failed', 'Микрофон недоступен')
    })
  }

  private async publishReconnect(): Promise<void> {
    if (this.room === null || this.capability === null) return
    this.clientSequence += 1
    const payload = new TextEncoder().encode(JSON.stringify({
      schema_version: CLIENT_CONTROL_VERSION,
      session_id: this.capability.session_id,
      sequence: this.clientSequence,
      type: 'client.reconnected',
    }))
    try {
      await this.room.localParticipant.publishData(payload, {
        reliable: true,
        topic: CLIENT_CONTROL_TOPIC,
      })
    } catch {
      this.callbacks.onConnection('failed', 'Не удалось восстановить сессию')
    }
  }
}
