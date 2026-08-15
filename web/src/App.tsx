import { useCallback, useState } from 'react'
import type { AvatarHostV1 } from './avatar/AvatarHost'
import { useVoiceSession } from './VoiceSessionContext'
import { VoiceShell } from './ui/VoiceShell'
import './styles.css'

export interface AppProps {
  avatarHost: AvatarHostV1
  buildVersion: string
}

/** Thin composition adapter from the existing voice-session reducer to the modular UI shell. */
export default function App({ avatarHost, buildVersion }: AppProps) {
  const {
    state,
    audioContainerRef,
    connect,
    disconnect,
    resumeAudio,
    toggleMicrophone,
    subscribeSpeechEnvelope,
    downloadDiagnostics,
  } = useVoiceSession()
  const [connectAttempted, setConnectAttempted] = useState(false)

  const beginConnect = useCallback(() => {
    setConnectAttempted(true)
    void connect()
  }, [connect])

  const endSession = useCallback(() => {
    setConnectAttempted(false)
    void disconnect()
  }, [disconnect])

  return (
    <VoiceShell
      state={state}
      avatarHost={avatarHost}
      buildVersion={buildVersion}
      connectAttempted={connectAttempted}
      audioContainerRef={audioContainerRef}
      subscribeSpeechEnvelope={subscribeSpeechEnvelope}
      onConnect={beginConnect}
      onDisconnect={endSession}
      onResumeAudio={() => void resumeAudio()}
      onToggleMicrophone={() => void toggleMicrophone()}
      onDownloadDiagnostics={downloadDiagnostics}
    />
  )
}
