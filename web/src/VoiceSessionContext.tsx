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
import { VoiceClient } from './voiceClient'

interface VoiceSessionValue {
  state: VoiceState
  audioContainerRef: React.RefObject<HTMLDivElement | null>
  connect(): Promise<void>
  disconnect(): Promise<void>
  resumeAudio(): Promise<void>
}

const VoiceSessionContext = createContext<VoiceSessionValue | null>(null)

export function VoiceSessionProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(voiceReducer, initialVoiceState)
  const audioContainerRef = useRef<HTMLDivElement>(null)
  const clientRef = useRef<VoiceClient | null>(null)

  const disconnect = useCallback(async () => {
    const client = clientRef.current
    clientRef.current = null
    if (client !== null) await client.stop()
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
    })
    clientRef.current = client
    try {
      await client.start()
    } catch (error) {
      await client.stop()
      clientRef.current = null
      dispatch({
        type: 'connection',
        connection: 'failed',
        error: error instanceof Error ? error.message : 'Локальный голосовой путь недоступен',
      })
    }
  }, [disconnect])

  const resumeAudio = useCallback(async () => {
    try {
      await clientRef.current?.resumeAudio()
      dispatch({ type: 'audio-blocked', blocked: false })
    } catch {
      dispatch({ type: 'audio-blocked', blocked: true })
    }
  }, [])

  useEffect(() => () => {
    void clientRef.current?.stop()
    clientRef.current = null
  }, [])

  const value = useMemo(() => ({
    state,
    audioContainerRef,
    connect,
    disconnect,
    resumeAudio,
  }), [state, connect, disconnect, resumeAudio])

  return <VoiceSessionContext.Provider value={value}>{children}</VoiceSessionContext.Provider>
}

export function useVoiceSession(): VoiceSessionValue {
  const value = useContext(VoiceSessionContext)
  if (value === null) throw new Error('useVoiceSession must be inside VoiceSessionProvider')
  return value
}
