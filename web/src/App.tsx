import { useVoiceSession } from './VoiceSessionContext'
import type { ConnectionState, TurnHistoryItem, TurnOutcome, TurnPhase } from './state'
import './styles.css'

const connectionLabels: Record<ConnectionState, string> = {
  idle: 'Отключено',
  connecting: 'Готовлю голос…',
  ready: 'Готово',
  reconnecting: 'Переподключение…',
  closed: 'Соединение закрыто',
  failed: 'Недоступно',
}

const phaseLabels: Record<TurnPhase, string> = {
  idle: 'Ожидание',
  listening: 'Слушаю',
  thinking: 'Думаю',
  speaking: 'Отвечаю',
}

const outcomeLabels: Record<TurnOutcome, string> = {
  completed: 'Завершено',
  interrupted: 'Прервано',
  failed: 'Ошибка',
}

function metric(value: number | null): string {
  return value === null ? '—' : `${Math.round(value)} мс`
}

export function historyUserText(item: TurnHistoryItem): string {
  if (item.user) return item.user
  return item.outcome === 'failed' ? 'Речь не распознана' : 'Распознаю речь…'
}

function ConnectionCard() {
  const { state } = useVoiceSession()
  return (
    <section className="card connection-card" aria-live="polite">
      <div>
        <p className="eyebrow">Локальная сессия</p>
        <h2>{connectionLabels[state.connection]}</h2>
      </div>
      <span className={`status-dot status-${state.connection}`} aria-hidden="true" />
      {state.connection === 'failed' && state.error && (
        <p className="error" role="alert">{state.error}</p>
      )}
    </section>
  )
}

function ConversationHistory() {
  const { state } = useVoiceSession()
  return (
    <section className="card turn-card" aria-live="polite">
      <div className="turn-heading">
        <p className="eyebrow">Диалог</p>
        <span className={`phase phase-${state.phase}`}>{phaseLabels[state.phase]}</span>
      </div>
      {state.history.length === 0 ? (
        <p>История появится после речи.</p>
      ) : state.history.map((item) => (
        <article className="history-turn" key={item.turnId}>
          <div className="conversation-block">
            <h3>Вы</h3>
            <p>{historyUserText(item)}</p>
          </div>
          <div className="conversation-block response">
            <h3>Агент</h3>
            <p>{item.assistant || 'Формирую ответ…'}</p>
          </div>
          <p className="technical">
            {item.outcome === null ? phaseLabels[state.phase] : outcomeLabels[item.outcome]}
            {item.audioUnavailable ? ' · Аудио недоступно' : ''}
          </p>
          <dl className="turn-metrics">
            <div>
              <dt>Endpoint → текст</dt>
              <dd>{metric(item.endpointToFirstVisibleMs)}</dd>
            </div>
            <div>
              <dt>Endpoint → server PCM</dt>
              <dd>{metric(item.endpointToFirstAcceptedPcmMs)}</dd>
            </div>
          </dl>
        </article>
      ))}
      {state.droppedEvents > 0 && (
        <p className="technical">Отклонено некорректных или устаревших событий: {state.droppedEvents}</p>
      )}
    </section>
  )
}

export function MicrophoneControl({
  enabled,
  transitioning,
  onToggle,
}: {
  enabled: boolean
  transitioning: boolean
  onToggle(): void
}) {
  return (
    <button
      type="button"
      className={`microphone-toggle ${enabled ? 'microphone-on' : 'microphone-off'}`}
      aria-pressed={enabled}
      aria-busy={transitioning}
      onClick={onToggle}
    >
      Микрофон: {enabled ? 'включён' : 'выключен'}
      {transitioning ? '…' : ''}
    </button>
  )
}

function Controls() {
  const {
    state,
    audioContainerRef,
    connect,
    disconnect,
    resumeAudio,
    toggleMicrophone,
    downloadDiagnostics,
  } = useVoiceSession()
  const active = ['connecting', 'ready', 'reconnecting'].includes(state.connection)
  return (
    <section className="controls" aria-label="Управление голосовой сессией">
      <button type="button" className="primary" onClick={() => void connect()} disabled={active}>
        Подключить микрофон
      </button>
      {active && state.microphoneAvailable && (
        <MicrophoneControl
          enabled={state.microphoneEnabled}
          transitioning={state.microphoneTransitioning}
          onToggle={() => void toggleMicrophone()}
        />
      )}
      <button type="button" onClick={() => void disconnect()} disabled={!active}>
        Отключиться
      </button>
      {state.audioBlocked && (
        <button type="button" onClick={() => void resumeAudio()}>
          Разрешить звук
        </button>
      )}
      <button type="button" onClick={downloadDiagnostics}>
        Скачать диагностику
      </button>
      {active && state.microphoneAvailable && state.microphoneError && (
        <p className="microphone-error" role="alert">{state.microphoneError}</p>
      )}
      <div ref={audioContainerRef} className="audio-mount" aria-hidden="true" />
    </section>
  )
}

export default function App() {
  return (
    <main className="app-shell">
      <header>
        <p className="eyebrow">Voice Agent v2 · Slice 6</p>
        <h1>Приватный голосовой диалог</h1>
        <p className="lede">Говорите естественно. Начните говорить во время ответа, чтобы прервать его.</p>
      </header>
      <div className="layout">
        <ConnectionCard />
        <ConversationHistory />
      </div>
      <Controls />
      <footer>Микрофон и server PCM идут через локальный LiveKit. Метрики не подтверждают физическую слышимость.</footer>
    </main>
  )
}
