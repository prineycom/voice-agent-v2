import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent, type RefObject } from 'react'
import { AvatarHostV1 } from '../avatar/AvatarHost'
import type { AvatarHealthV1 } from '../avatar/contract'
import type { SpeechEnvelopeObservation } from '../playback'
import type { VoiceState } from '../state'
import { AvatarViewport } from './AvatarViewport'
import { MicrophoneButton } from './controls/MicrophoneButton'
import { VoiceMenu } from './menu/VoiceMenu'
import { ConnectionIndicator, FullscreenConnectionOverlay } from './overlays/ConnectionOverlays'
import { SpeechOverlay } from './overlays/SpeechOverlay'
import { HistoryPanel } from './panels/HistoryPanel'
import { StatusPanel } from './panels/StatusPanel'
import { mapVoiceStateToUi } from './stateMapping'
import { useReducedMotion } from './useReducedMotion'
import { useTurnFailureLifecycle } from './useTurnFailureLifecycle'

interface VoiceShellProps {
  state: VoiceState
  avatarHost: AvatarHostV1
  buildVersion: string
  connectAttempted: boolean
  reviewLabel?: string
  audioContainerRef: RefObject<HTMLDivElement | null>
  subscribeSpeechEnvelope(listener: (observation: SpeechEnvelopeObservation) => void): () => void
  onConnect(): void
  onDisconnect(): void
  onResumeAudio(): void
  onToggleMicrophone(): void
  onDownloadDiagnostics(): void
}

const INITIAL_AVATAR_HEALTH: AvatarHealthV1 = {
  status: 'failed',
  activeModuleId: null,
  usingFallback: false,
  rejectedInputs: 0,
  renderFailures: 0,
}

export function VoiceShell({
  state,
  avatarHost,
  buildVersion,
  connectAttempted,
  reviewLabel,
  audioContainerRef,
  subscribeSpeechEnvelope,
  onConnect,
  onDisconnect,
  onResumeAudio,
  onToggleMicrophone,
  onDownloadDiagnostics,
}: VoiceShellProps) {
  const [avatarHealth, setAvatarHealth] = useState<AvatarHealthV1>(INITIAL_AVATAR_HEALTH)
  const [menuOpen, setMenuOpen] = useState(false)
  const [activePanel, setActivePanel] = useState<'history' | 'status' | null>(null)
  const [selectedTurnId, setSelectedTurnId] = useState<string | null>(null)
  const [readyFlash, setReadyFlash] = useState(false)
  const swipeStartRef = useRef<{ x: number; panelOpen: boolean } | null>(null)
  const menuButtonRef = useRef<HTMLButtonElement>(null)
  const connectionOverlayRef = useRef<HTMLDivElement>(null)
  const overlayRestoreFocusRef = useRef<HTMLElement | null>(null)
  const overlayWasActiveRef = useRef(false)
  const previousPanelRef = useRef(activePanel)
  const {
    userReducedMotion,
    systemReducedMotion,
    toggleUserReducedMotion,
  } = useReducedMotion()
  const turnFailureActive = useTurnFailureLifecycle(state)
  const model = useMemo(
    () => mapVoiceStateToUi(state, avatarHealth, turnFailureActive),
    [state, avatarHealth, turnFailureActive],
  )
  const motion = userReducedMotion
    ? 'static'
    : systemReducedMotion
      ? 'ambient-reduced'
      : 'full'
  const effectiveSelectedTurnId = selectedTurnId !== null
    && model.history.some((item) => item.turnId === selectedTurnId)
    ? selectedTurnId
    : model.history.at(-1)?.turnId ?? null
  const connectionOverlayActive = model.connection !== 'ready' || readyFlash
  const onAvatarHealth = useCallback((health: AvatarHealthV1) => setAvatarHealth(health), [])

  useEffect(() => {
    if (previousPanelRef.current !== null && activePanel === null) menuButtonRef.current?.focus()
    previousPanelRef.current = activePanel
  }, [activePanel])

  useLayoutEffect(() => {
    if (state.connection !== 'ready') {
      setReadyFlash(false)
      return
    }
    setReadyFlash(true)
    const timer = window.setTimeout(() => setReadyFlash(false), 650)
    return () => window.clearTimeout(timer)
  }, [state.connection])

  useLayoutEffect(() => {
    if (connectionOverlayActive) {
      if (!overlayWasActiveRef.current) {
        const activeElement = document.activeElement
        overlayRestoreFocusRef.current = activeElement instanceof HTMLElement && activeElement !== document.body
          ? activeElement
          : null
      }
      const overlay = connectionOverlayRef.current
      if (overlay !== null && !overlay.contains(document.activeElement)) {
        const target = overlay.querySelector<HTMLButtonElement>('button:not(:disabled)') ?? overlay
        target.focus()
      }
    } else if (overlayWasActiveRef.current) {
      const restoreTarget = overlayRestoreFocusRef.current
      if (restoreTarget?.isConnected && restoreTarget.closest('[inert]') === null) restoreTarget.focus()
      else menuButtonRef.current?.focus()
      overlayRestoreFocusRef.current = null
    }
    overlayWasActiveRef.current = connectionOverlayActive
  }, [connectionOverlayActive, model.connection])

  const onPointerDown = (event: PointerEvent<HTMLElement>) => {
    const panelOpen = activePanel !== null
    if (panelOpen || event.clientX >= window.innerWidth - 28) {
      swipeStartRef.current = { x: event.clientX, panelOpen }
    }
  }
  const onPointerUp = (event: PointerEvent<HTMLElement>) => {
    const start = swipeStartRef.current
    swipeStartRef.current = null
    if (start === null) return
    const movement = event.clientX - start.x
    if (start.panelOpen && movement > 64) setActivePanel(null)
    if (!start.panelOpen && movement < -64) setActivePanel('history')
  }

  return (
    <main
      className="voice-shell"
      data-user-reduced-motion={userReducedMotion}
      data-system-reduced-motion={systemReducedMotion}
      data-avatar-state={model.avatarLifecycle}
    >
      <div
        className="voice-shell__content"
        inert={connectionOverlayActive}
        onPointerDown={onPointerDown}
        onPointerUp={onPointerUp}
      >
      <AvatarViewport
        host={avatarHost}
        lifecycle={model.avatarLifecycle}
        motion={motion}
        subscribeSpeechEnvelope={subscribeSpeechEnvelope}
        onHealth={onAvatarHealth}
      />

      <ConnectionIndicator
        label={model.connectionLabel}
        connection={model.connection}
        audioBlocked={model.audioBlocked}
        onResumeAudio={onResumeAudio}
        buildLabel={reviewLabel}
      />
      <SpeechOverlay text={model.response} complete={model.responseComplete} />
      <MicrophoneButton
        visualState={model.microphoneVisual}
        pressed={model.microphoneEnabled}
        available={model.microphoneAvailable && ['connecting', 'ready', 'reconnecting'].includes(model.connection)}
        transitioning={model.microphoneTransitioning}
        onToggle={onToggleMicrophone}
      />
      <VoiceMenu
        open={menuOpen}
        historyOpen={activePanel === 'history'}
        statusOpen={activePanel === 'status'}
        reducedMotion={userReducedMotion}
        onOpenChange={setMenuOpen}
        onDisconnect={onDisconnect}
        onToggleHistory={() => setActivePanel((current) => current === 'history' ? null : 'history')}
        onToggleStatus={() => setActivePanel((current) => current === 'status' ? null : 'status')}
        onToggleReducedMotion={toggleUserReducedMotion}
        triggerRef={menuButtonRef}
      />

      <HistoryPanel
        open={activePanel === 'history'}
        history={model.history}
        selectedTurnId={effectiveSelectedTurnId}
        onSelectTurn={setSelectedTurnId}
        onClose={() => setActivePanel(null)}
      />
      <StatusPanel
        open={activePanel === 'status'}
        components={model.components}
        ttsSummary={model.ttsSummary}
        history={model.history}
        selectedTurnId={effectiveSelectedTurnId}
        droppedEvents={model.droppedEvents}
        avatarHealth={avatarHealth}
        buildVersion={buildVersion}
        onDownloadDiagnostics={onDownloadDiagnostics}
        onClose={() => setActivePanel(null)}
      />

      {model.microphoneError && <p className="visually-hidden" role="alert">MICROPHONE CONTROL FAILED</p>}
      <div ref={audioContainerRef} className="audio-mount" aria-hidden="true" />
      </div>
      <FullscreenConnectionOverlay
        connection={model.connection}
        readyFlash={readyFlash}
        connectAttempted={connectAttempted}
        onReconnect={onConnect}
        overlayRef={connectionOverlayRef}
      />
    </main>
  )
}
