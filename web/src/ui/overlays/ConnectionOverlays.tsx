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
}: {
  connection: ConnectionState
  readyFlash: boolean
  connectAttempted: boolean
  onReconnect(): void
}) {
  if (connection === 'ready' && !readyFlash) return null
  if (connection === 'ready') {
    return <div className="connection-overlay connection-overlay--ready" role="status">READY</div>
  }
  if (connection === 'connecting' || (connection === 'idle' && !connectAttempted)) {
    return <div className="connection-overlay" role="status">CONNECTING…</div>
  }
  if (connection === 'reconnecting') {
    return <div className="connection-overlay connection-overlay--warning" role="status">RECONNECTING…</div>
  }
  return (
    <div className="connection-overlay connection-overlay--error" role="alert">
      <p>CONNECTION LOST</p>
      <button type="button" className="neon-action" onClick={onReconnect}>RECONNECT</button>
    </div>
  )
}
