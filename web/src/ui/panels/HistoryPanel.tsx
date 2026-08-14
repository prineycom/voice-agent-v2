import type { TurnHistoryItem, TurnOutcome } from '../../state'

const OUTCOME_LABELS: Record<TurnOutcome, string> = {
  completed: 'COMPLETED',
  interrupted: 'INTERRUPTED',
  failed: 'FAILED',
}

export function historyUserText(item: TurnHistoryItem): string {
  if (item.user) return item.user
  return item.outcome === 'failed' ? 'Speech was not recognized.' : 'Listening…'
}

export function HistoryPanel({
  open,
  history,
  onClose,
}: {
  open: boolean
  history: TurnHistoryItem[]
  onClose(): void
}) {
  return (
    <aside
      className={`slide-panel history-panel${open ? ' slide-panel--open' : ''}`}
      aria-hidden={!open}
      aria-label="Conversation history"
    >
      <header className="panel-header">
        <h2>HISTORY</h2>
        <button type="button" className="panel-close" aria-label="Close history" onClick={onClose}>×</button>
      </header>
      <div className="panel-scroll" aria-live="polite">
        {history.length === 0 ? (
          <p className="panel-empty">NO TURNS YET</p>
        ) : [...history].reverse().map((item) => (
          <article className="history-item" key={item.turnId}>
            <p className="panel-label">USER</p>
            <p className="dialogue-text">{historyUserText(item)}</p>
            <p className="panel-label">AGENT</p>
            <p className="dialogue-text">{item.assistant || 'Preparing response…'}</p>
            <p className={`outcome outcome--${item.outcome ?? 'active'}`}>
              {item.outcome === null ? 'ACTIVE' : OUTCOME_LABELS[item.outcome]}
              {item.audioUnavailable ? ' · AUDIO UNAVAILABLE' : ''}
            </p>
          </article>
        ))}
      </div>
    </aside>
  )
}
