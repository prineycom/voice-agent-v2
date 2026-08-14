export function VoiceMenu({
  open,
  historyOpen,
  statusOpen,
  reducedMotion,
  onOpenChange,
  onDisconnect,
  onToggleHistory,
  onToggleStatus,
  onToggleReducedMotion,
}: {
  open: boolean
  historyOpen: boolean
  statusOpen: boolean
  reducedMotion: boolean
  onOpenChange(open: boolean): void
  onDisconnect(): void
  onToggleHistory(): void
  onToggleStatus(): void
  onToggleReducedMotion(): void
}) {
  const select = (action: () => void) => {
    action()
    onOpenChange(false)
  }
  return (
    <div className="voice-menu">
      <button
        type="button"
        className="menu-button"
        aria-label="Open menu"
        aria-expanded={open}
        aria-controls="voice-menu-items"
        onClick={() => onOpenChange(!open)}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4 7h16M4 12h16M4 17h16" />
        </svg>
      </button>
      {open && (
        <div id="voice-menu-items" className="voice-menu__items" role="menu">
          <button type="button" role="menuitem" onClick={() => select(onDisconnect)}>DISCONNECT</button>
          <button
            type="button"
            role="menuitemcheckbox"
            aria-checked={historyOpen}
            onClick={() => select(onToggleHistory)}
          >
            HISTORY <span aria-hidden="true">{historyOpen ? 'ON' : 'OFF'}</span>
          </button>
          <button
            type="button"
            role="menuitemcheckbox"
            aria-checked={statusOpen}
            onClick={() => select(onToggleStatus)}
          >
            STATUS <span aria-hidden="true">{statusOpen ? 'ON' : 'OFF'}</span>
          </button>
          <button
            type="button"
            role="menuitemcheckbox"
            aria-checked={reducedMotion}
            onClick={() => select(onToggleReducedMotion)}
          >
            REDUCE MOTION <span aria-hidden="true">{reducedMotion ? 'ON' : 'OFF'}</span>
          </button>
        </div>
      )}
    </div>
  )
}
