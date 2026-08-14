import { useEffect, useRef, useState } from 'react'
import type { AvatarHealthV1 } from '../../avatar/contract'
import type { TurnHistoryItem } from '../../state'
import type { UiSystemComponent } from '../stateMapping'

type StatusTab = 'system' | 'timeline'

function milliseconds(value: number | null): string {
  return value === null ? 'NOT OBSERVED' : `${Math.round(value)} MS`
}

function SystemTab({
  components,
  ttsSummary,
  avatarHealth,
  buildVersion,
}: {
  components: UiSystemComponent[]
  ttsSummary: string
  avatarHealth: AvatarHealthV1
  buildVersion: string
}) {
  return (
    <div className="status-system">
      <ul className="component-health">
        {components.map((component) => (
          <li key={component.id}>
            <span>{component.label}</span>
            <span className={`health health--${component.health.toLowerCase()}`}>{component.health}</span>
          </li>
        ))}
      </ul>
      <dl className="configuration-list">
        <div><dt>PROVIDER</dt><dd>LOCAL / SERVER-VERIFIED</dd></div>
        <div><dt>VOICE</dt><dd>{ttsSummary}</dd></div>
        <div><dt>AVATAR MODULE</dt><dd>{avatarHealth.activeModuleId ?? 'UNAVAILABLE'}</dd></div>
        <div><dt>AVATAR INPUT DROPS</dt><dd>{avatarHealth.rejectedInputs}</dd></div>
        <div><dt>BUILD</dt><dd className="build-version">{buildVersion}</dd></div>
      </dl>
    </div>
  )
}

function TimelineTab({
  item,
  droppedEvents,
  onDownloadDiagnostics,
}: {
  item: TurnHistoryItem | null
  droppedEvents: number
  onDownloadDiagnostics(): void
}) {
  return (
    <div className="status-timeline">
      {item === null ? (
        <p className="panel-empty">NO TURN TIMELINE</p>
      ) : (
        <>
          <p className="timeline-turn">TURN {item.turnId}</p>
          <ol className="timeline-list">
            <li><span>ENDPOINT</span><strong>OBSERVED</strong></li>
            <li><span>STT FINAL</span><strong>{item.user ? 'OBSERVED' : 'NOT OBSERVED'}</strong></li>
            <li><span>FIRST VISIBLE RESPONSE</span><strong>{milliseconds(item.endpointToFirstVisibleMs)}</strong></li>
            <li><span>SERVER-ACCEPTED PCM</span><strong>{milliseconds(item.endpointToFirstAcceptedPcmMs)}</strong></li>
            <li><span>COMPLETION</span><strong>{item.outcome?.toUpperCase() ?? 'ACTIVE'}</strong></li>
          </ol>
        </>
      )}
      <p className="drop-count">REJECTED EVENTS · {droppedEvents}</p>
      <button type="button" className="diagnostics-button" onClick={onDownloadDiagnostics}>
        DOWNLOAD DIAGNOSTICS
      </button>
    </div>
  )
}

export function StatusPanel({
  open,
  components,
  ttsSummary,
  history,
  selectedTurnId,
  droppedEvents,
  avatarHealth,
  buildVersion,
  onDownloadDiagnostics,
  onClose,
}: {
  open: boolean
  components: UiSystemComponent[]
  ttsSummary: string
  history: TurnHistoryItem[]
  selectedTurnId: string | null
  droppedEvents: number
  avatarHealth: AvatarHealthV1
  buildVersion: string
  onDownloadDiagnostics(): void
  onClose(): void
}) {
  const [tab, setTab] = useState<StatusTab>('system')
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const selectedTurn = history.find((item) => item.turnId === selectedTurnId) ?? history.at(-1) ?? null
  useEffect(() => {
    if (open) closeButtonRef.current?.focus()
  }, [open])

  return (
    <aside
      className={`slide-panel status-panel${open ? ' slide-panel--open' : ''}`}
      aria-hidden={!open}
      aria-label="Detailed status"
      inert={!open}
    >
      <header className="panel-header">
        <h2>STATUS</h2>
        <button ref={closeButtonRef} type="button" className="panel-close" aria-label="Close status" onClick={onClose}>×</button>
      </header>
      <div className="panel-tabs" role="tablist" aria-label="Status view">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'system'}
          onClick={() => setTab('system')}
        >SYSTEM</button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'timeline'}
          onClick={() => setTab('timeline')}
        >TIMELINE</button>
      </div>
      <div className="panel-scroll">
        {tab === 'system' ? (
          <SystemTab
            components={components}
            ttsSummary={ttsSummary}
            avatarHealth={avatarHealth}
            buildVersion={buildVersion}
          />
        ) : (
          <TimelineTab
            item={selectedTurn}
            droppedEvents={droppedEvents}
            onDownloadDiagnostics={onDownloadDiagnostics}
          />
        )}
      </div>
    </aside>
  )
}
