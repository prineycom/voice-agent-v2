import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from 'react'
import { initialVoiceState, voiceReducer, type VoiceState } from './state'
import type { SpeechEnvelopeObservation } from './playback'
import { VoiceClient } from './voiceClient'

interface VoiceSessionValue {
  state: VoiceState
  audioContainerRef: React.RefObject<HTMLDivElement | null>
  connect(): Promise<void>
  disconnect(): Promise<void>
  resumeAudio(): Promise<void>
  toggleMicrophone(): Promise<void>
  subscribeSpeechEnvelope(listener: (observation: SpeechEnvelopeObservation) => void): () => void
  downloadDiagnostics(): void
}

const VoiceSessionContext = createContext<VoiceSessionValue | null>(null)

export function VoiceSessionProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(voiceReducer, initialVoiceState)
  const audioContainerRef = useRef<HTMLDivElement>(null)
  const clientRef = useRef<VoiceClient | null>(null)
  const envelopeListenersRef = useRef(new Set<(observation: SpeechEnvelopeObservation) => void>())

  const disconnect = useCallback(async () => {
    const client = clientRef.current
    if (client === null) return
    try {
      await client.stop()
    } finally {
      if (clientRef.current === client) clientRef.current = null
      dispatch({ type: 'reset' })
    }
  }, [])

  const connect = useCallback(async () => {
    if (audioContainerRef.current === null) return
    await disconnect()
    const client = new VoiceClient(audioContainerRef.current, {
      onSession: (capability) => dispatch({ type: 'session-created', capability }),
      onConnection: (connection, error) => dispatch({ type: 'connection', connection, error }),
      onControl: (event) => dispatch({ type: 'control', event }),
      onDrop: () => dispatch({ type: 'drop' }),
      onAudioBlocked: (blocked) => dispatch({ type: 'audio-blocked', blocked }),
      onSpeechEnvelope: (observation) => {
        for (const listener of envelopeListenersRef.current) listener(observation)
      },
      onSpeechEnvelopeStatus: (status) => dispatch({ type: 'speech-envelope-status', status }),
      onMicrophoneState: (enabled, transitioning, error) => {
        dispatch({ type: 'microphone', enabled, transitioning, error })
      },
    })
    clientRef.current = client
    try {
      await client.start()
    } catch (error) {
      try {
        await client.stop()
      } catch {}
      if (clientRef.current === client) {
        dispatch({
          type: 'connection',
          connection: 'failed',
          error: error instanceof Error ? error.message : 'Локальный голосовой путь недоступен',
        })
      }
    }
  }, [disconnect])

  const downloadDiagnostics = useCallback(() => {
    clientRef.current?.downloadDiagnostics()
  }, [])

  const subscribeSpeechEnvelope = useCallback((
    listener: (observation: SpeechEnvelopeObservation) => void,
  ) => {
    envelopeListenersRef.current.add(listener)
    return () => envelopeListenersRef.current.delete(listener)
  }, [])

  const resumeAudio = useCallback(async () => {
    try {
      await clientRef.current?.resumeAudio()
      dispatch({ type: 'audio-blocked', blocked: false })
    } catch {
      dispatch({ type: 'audio-blocked', blocked: true })
    }
  }, [])

  const toggleMicrophone = useCallback(async () => {
    await clientRef.current?.toggleMicrophone()
  }, [])

  useEffect(() => () => {
    void clientRef.current?.stop()
    clientRef.current = null
    envelopeListenersRef.current.clear()
  }, [])

  const value = useMemo(() => ({
    state,
    audioContainerRef,
    connect,
    disconnect,
    resumeAudio,
    toggleMicrophone,
    subscribeSpeechEnvelope,
    downloadDiagnostics,
  }), [
    state, connect, disconnect, resumeAudio, toggleMicrophone,
    subscribeSpeechEnvelope, downloadDiagnostics,
  ])

  return <VoiceSessionContext.Provider value={value}>{children}</VoiceSessionContext.Provider>
}

export function useVoiceSession(): VoiceSessionValue {
  const value = useContext(VoiceSessionContext)
  if (value === null) throw new Error('useVoiceSession must be inside VoiceSessionProvider')
  return value
}
