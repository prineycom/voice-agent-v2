import { useVoiceSession } from './VoiceSessionContext'
import type { ConnectionState, TurnPhase } from './state'
import './styles.css'

const connectionLabels: Record<ConnectionState, string> = {
  idle: 'Отключено',
  connecting: 'Подключение…',
  ready: 'Готово',
  reconnecting: 'Переподключение…',
  closed: 'Соединение закрыто',
  failed: 'Недоступно',
}

const phaseLabels: Record<TurnPhase, string> = {
  idle: 'Ожидание',
  listening: 'Слушаю',
  transcribing: 'Распознаю',
  thinking: 'Думаю',
  speaking: 'Отвечаю',
  completed: 'Готово',
  interrupted: 'Прервано',
  failed: 'Ошибка',
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
      {state.error && <p className="error" role="alert">{state.error}</p>}
    </section>
  )
}

function TurnCard() {
  const { state } = useVoiceSession()
  return (
    <section className="card turn-card" aria-live="polite">
      <div className="turn-heading">
        <p className="eyebrow">Текущий ход</p>
        <span className={`phase phase-${state.phase}`}>{phaseLabels[state.phase]}</span>
      </div>
      <div className="conversation-block">
        <h3>Вы</h3>
        <p>{state.transcript || 'Транскрипт появится после речи.'}</p>
      </div>
      <div className="conversation-block response">
        <h3>Агент</h3>
        <p>{state.response || 'Ответ появится здесь и прозвучит через LiveKit.'}</p>
      </div>
      {state.droppedEvents > 0 && (
        <p className="technical">Отклонено некорректных или устаревших событий: {state.droppedEvents}</p>
      )}
    </section>
  )
}

function Controls() {
  const { state, audioContainerRef, connect, disconnect, resumeAudio, downloadDiagnostics } = useVoiceSession()
  const active = ['connecting', 'ready', 'reconnecting'].includes(state.connection)
  return (
    <section className="controls" aria-label="Управление голосовой сессией">
      <button type="button" className="primary" onClick={() => void connect()} disabled={active}>
        Подключить микрофон
      </button>
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
        <TurnCard />
      </div>
      <Controls />
      <footer>Микрофон и аудио идут через локальный LiveKit. STT и TTS остаются на хосте.</footer>
    </main>
  )
}
