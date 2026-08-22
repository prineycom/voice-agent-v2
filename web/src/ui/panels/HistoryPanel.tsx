import { useEffect, useRef } from 'react'
import type { TurnHistoryItem, TurnOutcome } from '../../state'

const OUTCOME_LABELS: Record<TurnOutcome, string> = {
  completed: 'COMPLETED',
  interrupted: 'INTERRUPTED',
  failed: 'FAILED',
}

export function actionOutcomeText(item: TurnHistoryItem): string {
  if (item.actionOutcome === 'completed') return `ACTION COMPLETED · ${item.operationCount ?? 0}`
  if (item.actionOutcome === 'failed') return `ACTION FAILED · ${item.operationCount ?? 0}`
  if (item.actionOutcome === 'no_operation') return 'NO ACTION EXECUTED'
  return 'ACTION NOT YET RESOLVED'
}

export function speechOutcomeText(item: TurnHistoryItem): string {
  if (item.speechOutcome === 'delivered') return 'SPEECH DELIVERED'
  if (item.speechOutcome === 'failed' || item.audioUnavailable) return 'SPEECH DELIVERY FAILED'
  if (item.speechOutcome === 'interrupted') return 'SPEECH INTERRUPTED'
  return 'SPEECH NOT DELIVERED'
}

export function historyUserText(item: TurnHistoryItem): string {
  if (item.user) return item.user
  return item.outcome === 'failed' ? 'Speech was not recognized.' : 'Listening…'
}

export function HistoryPanel({
  open,
  history,
  selectedTurnId,
  onSelectTurn,
  onClose,
}: {
  open: boolean
  history: TurnHistoryItem[]
  selectedTurnId: string | null
  onSelectTurn(turnId: string): void
  onClose(): void
}) {
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (open) closeButtonRef.current?.focus()
  }, [open])

  return (
    <aside
      className={`slide-panel history-panel${open ? ' slide-panel--open' : ''}`}
      aria-hidden={!open}
      aria-label="Conversation history"
      inert={!open}
    >
      <header className="panel-header">
        <h2>HISTORY</h2>
        <button ref={closeButtonRef} type="button" className="panel-close" aria-label="Close history" onClick={onClose}>×</button>
      </header>
      <div className="panel-scroll" aria-live="polite">
        {history.length === 0 ? (
          <p className="panel-empty">NO TURNS YET</p>
        ) : [...history].reverse().map((item) => (
          <article className="history-item" key={item.turnId}>
            <button
              type="button"
              className="history-item__select"
              aria-label={`Select turn ${item.turnId}`}
              aria-pressed={selectedTurnId === item.turnId}
              onClick={() => onSelectTurn(item.turnId)}
            >
              <span className="panel-label">USER</span>
              <span className="dialogue-text">{historyUserText(item)}</span>
              <span className="panel-label">AGENT</span>
              <span className="dialogue-text">{item.assistant || 'Preparing response…'}</span>
              <span className={`outcome outcome--${item.outcome ?? 'active'}`}>
                {item.outcome === null ? 'ACTIVE' : OUTCOME_LABELS[item.outcome]}
                {item.audioUnavailable ? ' · AUDIO UNAVAILABLE' : ''}
              </span>
              <span className={`outcome outcome--${item.actionOutcome === 'failed' ? 'failed' : item.actionOutcome === 'completed' ? 'completed' : 'active'}`}>
                {actionOutcomeText(item)}
              </span>
              <span className={`outcome outcome--${item.speechOutcome === 'failed' || item.audioUnavailable ? 'failed' : item.speechOutcome === 'delivered' ? 'completed' : 'active'}`}>
                {speechOutcomeText(item)}
              </span>
            </button>
          </article>
        ))}
      </div>
    </aside>
  )
}
