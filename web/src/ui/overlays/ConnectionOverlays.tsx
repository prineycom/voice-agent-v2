import type { RefObject } from 'react'
import type { ConnectionState } from '../../state'

export function ConnectionIndicator({
  label,
  connection,
  audioBlocked,
  onResumeAudio,
  buildLabel,
}: {
  label: string
  connection: ConnectionState
  audioBlocked: boolean
  onResumeAudio(): void
  buildLabel?: string
}) {
  if (connection !== 'ready') return null
  const content = (
    <>
      <span className="connection-indicator__dot" aria-hidden="true" />
      <span>{audioBlocked ? 'ENABLE AUDIO' : label}</span>
      {buildLabel && <span className="connection-indicator__build">{buildLabel}</span>}
    </>
  )
  return audioBlocked ? (
    <button
      type="button"
      className="connection-indicator connection-indicator--action"
      onClick={onResumeAudio}
    >
      {content}
    </button>
  ) : (
    <div className="connection-indicator" role="status" aria-label={`${label}${buildLabel ? `, ${buildLabel}` : ''}`}>
      {content}
    </div>
  )
}

export function FullscreenConnectionOverlay({
  connection,
  readyFlash,
  connectAttempted,
  onReconnect,
  overlayRef,
}: {
  connection: ConnectionState
  readyFlash: boolean
  connectAttempted: boolean
  onReconnect(): void
  overlayRef: RefObject<HTMLDivElement | null>
}) {
  if (connection === 'ready' && !readyFlash) return null
  if (connection === 'ready') {
    return (
      <div
        ref={overlayRef}
        className="connection-overlay connection-overlay--ready"
        role="dialog"
        aria-modal="true"
        aria-label="Ready"
        tabIndex={-1}
      >READY</div>
    )
  }
  if (connection === 'idle' && !connectAttempted) {
    return (
      <div
        ref={overlayRef}
        className="connection-overlay connection-overlay--disconnected"
        role="dialog"
        aria-modal="true"
        aria-label="Voice session disconnected"
        tabIndex={-1}
      >
        <p>DISCONNECTED</p>
        <button type="button" className="neon-action" onClick={onReconnect}>CONNECT</button>
      </div>
    )
  }
  if (connection === 'connecting' || (connection === 'idle' && connectAttempted)) {
    return (
      <div
        ref={overlayRef}
        className="connection-overlay"
        role="dialog"
        aria-modal="true"
        aria-label="Connecting"
        tabIndex={-1}
      >CONNECTING…</div>
    )
  }
  if (connection === 'reconnecting') {
    return (
      <div
        ref={overlayRef}
        className="connection-overlay connection-overlay--warning"
        role="dialog"
        aria-modal="true"
        aria-label="Reconnecting"
        tabIndex={-1}
      >RECONNECTING…</div>
    )
  }
  return (
    <div
      ref={overlayRef}
      className="connection-overlay connection-overlay--error"
      role="alertdialog"
      aria-modal="true"
      aria-label="Connection lost"
      tabIndex={-1}
    >
      <p>CONNECTION LOST</p>
      <button type="button" className="neon-action" onClick={onReconnect}>RECONNECT</button>
    </div>
  )
}
