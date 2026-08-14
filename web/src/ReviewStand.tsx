import { useRef, useState } from 'react'
import type { AvatarHostV1 } from './avatar/AvatarHost'
import { CONTROL_VERSION, initialVoiceState, type VoiceState } from './state'
import { VoiceShell } from './ui/VoiceShell'

function reviewState(): VoiceState {
  return {
    ...initialVoiceState,
    connection: 'ready',
    sessionId: 'review-stand',
    streamEpoch: 1,
    lastSequence: 7,
    currentTurnId: 'turn-review-0001',
    currentTurnGeneration: 1,
    currentRequestId: 'request-review-0001',
    currentMediaGeneration: 1,
    currentTurnTerminal: false,
    lastTurnEvent: 'turn.speaking',
    phase: 'speaking',
    transcript: 'Расскажи, что ты видишь.',
    response: 'Я вижу спокойный неоновый интерфейс и готова продолжить разговор.',
    history: [{
      turnId: 'turn-review-0001',
      user: 'Расскажи, что ты видишь.',
      assistant: 'Я вижу спокойный неоновый интерфейс и готова продолжить разговор.',
      outcome: null,
      audioUnavailable: false,
      endpointToFirstVisibleMs: 418,
      endpointToFirstAcceptedPcmMs: 672,
    }],
    microphoneAvailable: true,
    microphoneEnabled: true,
    ttsProfile: {
      profile: 'silero-kseniya',
      backend: 'silero',
      speaker: 'kseniya',
      output_sample_rate_hz: 48_000,
      native_sample_rate_hz: 48_000,
      license: 'CC-BY-NC-SA-4.0',
      private_noncommercial_only: true,
    },
  }
}

export function ReviewStand({ avatarHost, buildVersion }: {
  avatarHost: AvatarHostV1
  buildVersion: string
}) {
  const [state, setState] = useState(reviewState)
  const audioContainerRef = useRef<HTMLDivElement>(null)
  const shortBuild = buildVersion.slice(0, 12)
  const reconnect = () => setState((current) => ({ ...current, connection: 'ready', error: null }))
  const disconnect = () => setState((current) => ({ ...current, connection: 'failed' }))
  const downloadDiagnostics = () => {
    const blob = new Blob([
      `${JSON.stringify({ mode: 'review-stand', build: buildVersion, control: CONTROL_VERSION })}\n`,
    ], { type: 'application/x-ndjson' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `voice-agent-review-${shortBuild}.jsonl`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  return (
    <VoiceShell
      state={state}
      avatarHost={avatarHost}
      buildVersion={buildVersion}
      reviewLabel={`REVIEW ${shortBuild}`}
      connectAttempted
      audioContainerRef={audioContainerRef}
      subscribeSpeechEnvelope={() => () => undefined}
      onConnect={reconnect}
      onDisconnect={disconnect}
      onResumeAudio={() => undefined}
      onToggleMicrophone={() => setState((current) => ({
        ...current,
        microphoneEnabled: !current.microphoneEnabled,
        microphoneError: null,
      }))}
      onDownloadDiagnostics={downloadDiagnostics}
    />
  )
}
