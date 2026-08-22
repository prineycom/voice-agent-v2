import { useEffect, useRef, useState } from 'react'
import type { AvatarHealthV1 } from '../../avatar/contract'
import type { SpeechEnvelopeStatus } from '../../playback'
import type { TurnHistoryItem } from '../../state'
import type { UiSystemComponent } from '../stateMapping'

type StatusTab = 'system' | 'timeline'

function milliseconds(value: number | null): string {
  return value === null ? 'NOT OBSERVED' : `${Math.round(value)} MS`
}

function SystemTab({
  components,
  llmProviderSummary,
  llmModelSummary,
  ttsSummary,
  avatarHealth,
  speechEnvelopeStatus,
  buildVersion,
}: {
  components: UiSystemComponent[]
  llmProviderSummary: string
  llmModelSummary: string
  ttsSummary: string
  avatarHealth: AvatarHealthV1
  speechEnvelopeStatus: SpeechEnvelopeStatus
  buildVersion: string
}) {
  return (
    <div className="status-system">
      <ul className="component-health">
        {components.map((component) => (
          <li key={component.id}>
            <span>{component.label}</span>
            <span className={`health health--${component.health.toLowerCase()}`}>{component.health}</span>
            <small>
              LIVE {component.liveness} · READINESS {component.readiness} · {component.compatible === null ? 'COMPATIBILITY UNKNOWN' : component.compatible ? 'COMPATIBLE' : 'INCOMPATIBLE'}
              {component.reason ? ` · ${component.reason}` : ''}
            </small>
          </li>
        ))}
      </ul>
      <dl className="configuration-list">
        <div><dt>PROVIDER</dt><dd>{llmProviderSummary}</dd></div>
        <div><dt>MODEL</dt><dd>{llmModelSummary}</dd></div>
        <div><dt>VOICE</dt><dd>{ttsSummary}</dd></div>
        <div><dt>AVATAR MODULE</dt><dd>{avatarHealth.activeModuleId ?? 'UNAVAILABLE'}</dd></div>
        <div><dt>AVATAR INPUT DROPS</dt><dd>{avatarHealth.rejectedInputs}</dd></div>
        <div><dt>SPEECH ENVELOPE</dt><dd>{speechEnvelopeStatus.toUpperCase()}</dd></div>
        <div><dt>EXTERNAL TRANSFER</dt><dd>FALSE</dd></div>
        <div><dt>STT / TTS LOCATION</dt><dd>LOCAL / LOCAL</dd></div>
        <div><dt>AUTOMATIC FALLBACK</dt><dd>FALSE</dd></div>
        <div><dt>CONTENT CAPTURE</dt><dd>OFF BY DEFAULT</dd></div>
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
            <li><span>ENDPOINT → STT FINAL</span><strong>{milliseconds(item.endpointToSttFinalMs ?? null)}</strong></li>
            <li><span>LLM FIRST TOKEN</span><strong>{milliseconds(item.providerTimeToFirstTokenMs ?? null)}</strong></li>
            <li><span>LLM COMPLETION</span><strong>{milliseconds(item.providerCompletionMs ?? null)}</strong></li>
            <li><span>TTS FIRST AUDIO</span><strong>{milliseconds(item.ttsTimeToFirstAudioMs ?? null)}</strong></li>
            <li><span>FIRST VISIBLE RESPONSE</span><strong>{milliseconds(item.endpointToFirstVisibleMs)}</strong></li>
            <li><span>SERVER-ACCEPTED PCM</span><strong>{milliseconds(item.endpointToFirstAcceptedPcmMs)}</strong></li>
            <li><span>TOTAL TURN</span><strong>{milliseconds(item.totalTurnMs ?? null)}</strong></li>
            <li><span>CANCELLATION LATENCY</span><strong>{milliseconds(item.cancellationLatencyMs ?? null)}</strong></li>
            <li><span>SLOWEST STAGE</span><strong>{item.slowestStage?.toUpperCase() ?? 'NOT OBSERVED'}</strong></li>
            <li><span>TERMINAL</span><strong>{item.outcome?.toUpperCase() ?? 'ACTIVE'}</strong></li>
            <li><span>ACTION</span><strong>{item.actionOutcome === 'completed' ? `COMPLETED · ${item.operationCount ?? 0}` : item.actionOutcome === 'failed' ? `FAILED · ${item.operationCount ?? 0}` : item.actionOutcome === 'no_operation' ? 'NO OPERATION' : 'NOT YET RESOLVED'}</strong></li>
            <li><span>SPEECH</span><strong>{(item.speechOutcome ?? (item.audioUnavailable ? 'failed' : 'not_started')).replaceAll('_', ' ').toUpperCase()}</strong></li>
          </ol>
          <dl className="configuration-list timeline-metadata">
            <div><dt>PROVIDER</dt><dd>{item.providerMode?.toUpperCase() ?? 'NOT OBSERVED'}</dd></div>
            <div><dt>EXTERNAL TRANSFER</dt><dd>{item.externalTransfer === null || item.externalTransfer === undefined ? 'NOT OBSERVED' : String(item.externalTransfer).toUpperCase()}</dd></div>
            <div><dt>USAGE UNITS</dt><dd>{item.providerInputUnitCount ?? '—'} / {item.providerOutputUnitCount ?? '—'} / {item.providerTotalUnitCount ?? '—'}</dd></div>
            <div><dt>QUEUES PCM / SEGMENT</dt><dd>{item.pcmQueueMaxBlocks ?? '—'} / {item.segmentQueueMaxSegments ?? '—'}</dd></div>
            <div><dt>CANCELLATIONS / STALE DROPS</dt><dd>{item.cancellationCount ?? 0} / {item.staleDropCount ?? 0}</dd></div>
            <div><dt>CPU / RAM</dt><dd>{item.cpuUtilizationPercent ?? '—'}% / {item.hostRamUsedMib ?? '—'} MIB</dd></div>
            <div><dt>PROCESS RSS</dt><dd>{item.processRssMib ?? '—'} MIB</dd></div>
            <div><dt>GPU / VRAM</dt><dd>{item.gpuUtilizationPercent ?? '—'}% / {item.gpuVramUsedMib ?? '—'} MIB</dd></div>
            <div><dt>USER STATE</dt><dd>{item.userState?.toUpperCase() ?? 'AVAILABLE'}</dd></div>
          </dl>
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
  llmProviderSummary,
  llmModelSummary,
  ttsSummary,
  history,
  selectedTurnId,
  droppedEvents,
  avatarHealth,
  speechEnvelopeStatus,
  buildVersion,
  onDownloadDiagnostics,
  onClose,
}: {
  open: boolean
  components: UiSystemComponent[]
  llmProviderSummary: string
  llmModelSummary: string
  ttsSummary: string
  history: TurnHistoryItem[]
  selectedTurnId: string | null
  droppedEvents: number
  avatarHealth: AvatarHealthV1
  speechEnvelopeStatus: SpeechEnvelopeStatus
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
            llmProviderSummary={llmProviderSummary}
            llmModelSummary={llmModelSummary}
            ttsSummary={ttsSummary}
            avatarHealth={avatarHealth}
            speechEnvelopeStatus={speechEnvelopeStatus}
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
