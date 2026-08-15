import { createRef } from 'react'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AvatarHostV1 } from './avatar/AvatarHost'
import {
  AVATAR_HOST_INTERFACE_VERSION,
  AVATAR_REQUIRED_CAPABILITIES,
  type AvatarModuleV1,
} from './avatar/contract'
import { ReviewStand } from './ReviewStand'
import { initialVoiceState, type VoiceState } from './state'
import { AvatarViewport } from './ui/AvatarViewport'
import { VoiceShell } from './ui/VoiceShell'
import { historyUserText } from './ui/panels/HistoryPanel'
import { REDUCE_MOTION_STORAGE_KEY } from './ui/useReducedMotion'

function testAvatarHost(onUpdate = vi.fn(), onCancel = vi.fn()): AvatarHostV1 {
  return new AvatarHostV1([() => {
    let root: HTMLElement | null = null
    const module: AvatarModuleV1 = {
      manifest: {
        interfaceVersion: AVATAR_HOST_INTERFACE_VERSION,
        id: 'test-avatar',
        displayName: 'Test Avatar',
        capabilities: AVATAR_REQUIRED_CAPABILITIES,
        deterministic: true,
      },
      setFailureHandler: vi.fn(),
      mount(container) {
        root = document.createElement('div')
        root.dataset.testAvatar = 'mounted'
        container.replaceChildren(root)
      },
      update: onUpdate,
      cancel: onCancel,
      dispose() { root?.remove(); root = null },
    }
    return module
  }])
}

beforeEach(() => {
  localStorage.clear()
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockReturnValue({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  })
})

describe('Slice 7 modular shell', () => {
  it('labels terminal STT failure without inventing conversation content', () => {
    expect(historyUserText({
      turnId: 'turn-00000001',
      user: '',
      assistant: '',
      outcome: 'failed',
      audioUnavailable: false,
      endpointToFirstVisibleMs: null,
      endpointToFirstAcceptedPcmMs: null,
    })).toBe('Speech was not recognized.')
  })

  it('renders the avatar viewport and only the four intended steady-state overlay responsibilities', () => {
    const { container } = render(
      <ReviewStand avatarHost={testAvatarHost()} buildVersion="0123456789abcdef" />,
    )

    expect(screen.getByTestId('avatar-viewport')).toBeTruthy()
    expect(container.querySelector('.connection-indicator')).toBeTruthy()
    expect(container.querySelector('.speech-overlay')).toBeTruthy()
    expect(container.querySelector('.microphone-button')).toBeTruthy()
    expect(container.querySelector('.voice-menu')).toBeTruthy()
    expect(container.querySelectorAll('.card, footer, .controls')).toHaveLength(0)
    expect(screen.getByText('REVIEW 0123456789ab')).toBeTruthy()
  })

  it('provides exactly four menu items and toggles history and status panels', async () => {
    const user = userEvent.setup()
    render(<ReviewStand avatarHost={testAvatarHost()} buildVersion="build-test" />)

    await user.click(screen.getByRole('button', { name: 'Open menu' }))
    const menu = screen.getByRole('menu')
    expect([...menu.querySelectorAll('button')].map((button) => button.textContent?.trim())).toEqual([
      'DISCONNECT', 'HISTORY OFF', 'STATUS OFF', 'REDUCE MOTION OFF',
    ])

    await user.click(within(menu).getByRole('menuitemcheckbox', { name: /HISTORY/ }))
    const historyPanel = screen.getByLabelText('Conversation history')
    expect(historyPanel.getAttribute('aria-hidden')).toBe('false')
    expect(historyPanel.hasAttribute('inert')).toBe(false)
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close history' }))
    expect(screen.getByText('Расскажи, что ты видишь.')).toBeTruthy()
    const selectedTurn = screen.getByRole('button', { name: 'Select turn turn-review-0000' })
    await user.click(selectedTurn)
    expect(selectedTurn.getAttribute('aria-pressed')).toBe('true')

    await user.click(screen.getByRole('button', { name: 'Close history' }))
    expect(historyPanel.hasAttribute('inert')).toBe(true)
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Open menu' }))

    await user.click(screen.getByRole('button', { name: 'Open menu' }))
    await user.click(screen.getByRole('menuitemcheckbox', { name: /STATUS/ }))
    const statusPanel = screen.getByLabelText('Detailed status')
    expect(statusPanel.getAttribute('aria-hidden')).toBe('false')
    expect(statusPanel.hasAttribute('inert')).toBe(false)
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close status' }))
    expect(screen.getByRole('tab', { name: 'SYSTEM' })).toBeTruthy()
    await user.click(screen.getByRole('tab', { name: 'TIMELINE' }))
    expect(screen.getByText('TURN turn-review-0000')).toBeTruthy()
    expect(screen.getByText('FIRST VISIBLE RESPONSE')).toBeTruthy()
    expect(screen.getByText('SERVER-ACCEPTED PCM')).toBeTruthy()
    expect(screen.queryByText('LLM FIRST TOKEN')).toBeNull()
    expect(screen.queryByText('TTS FIRST AUDIO')).toBeNull()
  })

  it('scopes Timeline turn selection to the active session', async () => {
    const user = userEvent.setup()
    const avatarHost = testAvatarHost()
    const audioContainerRef = createRef<HTMLDivElement>()
    const reusedHistory = [{
      turnId: 'turn-00000001',
      user: 'First turn',
      assistant: 'First answer',
      outcome: 'completed' as const,
      audioUnavailable: false,
      endpointToFirstVisibleMs: 100,
      endpointToFirstAcceptedPcmMs: 200,
    }, {
      turnId: 'turn-00000002',
      user: 'Latest turn',
      assistant: 'Latest answer',
      outcome: 'completed' as const,
      audioUnavailable: false,
      endpointToFirstVisibleMs: 300,
      endpointToFirstAcceptedPcmMs: 400,
    }]
    const stateFor = (sessionId: string): VoiceState => ({
      ...initialVoiceState,
      connection: 'ready',
      sessionId,
      history: reusedHistory,
    })
    const shell = (state: VoiceState) => (
      <VoiceShell
        state={state}
        avatarHost={avatarHost}
        buildVersion="build-test"
        connectAttempted
        audioContainerRef={audioContainerRef}
        subscribeSpeechEnvelope={() => () => undefined}
        onConnect={() => undefined}
        onDisconnect={() => undefined}
        onResumeAudio={() => undefined}
        onToggleMicrophone={() => undefined}
        onDownloadDiagnostics={() => undefined}
      />
    )
    const { rerender } = render(shell(stateFor('session-one')))

    await user.click(screen.getByRole('button', { name: 'Open menu' }))
    await user.click(screen.getByRole('menuitemcheckbox', { name: /HISTORY/ }))
    await user.click(screen.getByRole('button', { name: 'Select turn turn-00000001' }))
    await user.click(screen.getByRole('button', { name: 'Close history' }))
    await user.click(screen.getByRole('button', { name: 'Open menu' }))
    await user.click(screen.getByRole('menuitemcheckbox', { name: /STATUS/ }))
    await user.click(screen.getByRole('tab', { name: 'TIMELINE' }))
    expect(screen.getByText('TURN turn-00000001')).toBeTruthy()

    rerender(shell(stateFor('session-two')))

    expect(screen.getByText('TURN turn-00000002')).toBeTruthy()
  })

  it('uses the menu disconnect action and actionable full-screen reconnect overlay', async () => {
    const user = userEvent.setup()
    render(<ReviewStand avatarHost={testAvatarHost()} buildVersion="build-test" />)

    await user.click(screen.getByRole('button', { name: 'Open menu' }))
    await user.click(screen.getByRole('menuitem', { name: 'DISCONNECT' }))
    expect(screen.getByRole('alertdialog', { name: 'Connection lost' }).textContent).toContain('CONNECTION LOST')
    const shellContent = document.querySelector('.voice-shell__content')
    expect(shellContent?.hasAttribute('inert')).toBe(true)
    const reconnect = screen.getByRole('button', { name: 'RECONNECT' })
    expect(document.activeElement).toBe(reconnect)
    await user.click(reconnect)
    expect(screen.getByRole('dialog', { name: 'Ready' })).toBeTruthy()
    await vi.waitFor(() => expect(screen.queryByRole('dialog', { name: 'Ready' })).toBeNull())
    expect(shellContent?.hasAttribute('inert')).toBe(false)
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Open menu' }))
  })

  it('suppresses avatar controls after interruption until the lifecycle resumes', () => {
    const update = vi.fn()
    const cancel = vi.fn()
    const host = testAvatarHost(update, cancel)
    const envelopeSubscription: {
      listener: ((observation: { level: number; observedAtMs: number }) => void) | null
    } = { listener: null }
    const subscribeSpeechEnvelope = (listener: (observation: { level: number; observedAtMs: number }) => void) => {
      envelopeSubscription.listener = listener
      return () => { envelopeSubscription.listener = null }
    }
    const onHealth = vi.fn()
    const { rerender } = render(
      <AvatarViewport
        host={host}
        lifecycle="speaking"
        motion="full"
        subscribeSpeechEnvelope={subscribeSpeechEnvelope}
        onHealth={onHealth}
      />,
    )
    const updatesBeforeCancel = update.mock.calls.length

    rerender(
      <AvatarViewport
        host={host}
        lifecycle="interrupted"
        motion="full"
        subscribeSpeechEnvelope={subscribeSpeechEnvelope}
        onHealth={onHealth}
      />,
    )
    expect(cancel).toHaveBeenCalledTimes(1)
    expect(update).toHaveBeenCalledTimes(updatesBeforeCancel)
    envelopeSubscription.listener?.({ level: 0.8, observedAtMs: performance.now() })
    expect(update).toHaveBeenCalledTimes(updatesBeforeCancel)

    rerender(
      <AvatarViewport
        host={host}
        lifecycle="idle"
        motion="full"
        subscribeSpeechEnvelope={subscribeSpeechEnvelope}
        onHealth={onHealth}
      />,
    )
    expect(update).toHaveBeenLastCalledWith(expect.objectContaining({
      lifecycle: 'idle',
      speechEnvelope: null,
    }))
  })

  it('maps the system reduced-motion preference to ambient-reduced avatar input', async () => {
    vi.mocked(window.matchMedia).mockReturnValue({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    } as unknown as MediaQueryList)
    const update = vi.fn()
    render(<ReviewStand avatarHost={testAvatarHost(update)} buildVersion="build-test" />)

    await vi.waitFor(() => expect(update).toHaveBeenCalledWith(expect.objectContaining({
      motion: 'ambient-reduced',
    })))
    expect(document.querySelector('.voice-shell')?.getAttribute('data-system-reduced-motion')).toBe('true')
  })

  it('shows speech-envelope degradation without reporting the avatar ready', async () => {
    const user = userEvent.setup()
    const audioContainerRef = createRef<HTMLDivElement>()
    render(
      <VoiceShell
        state={{
          ...initialVoiceState,
          connection: 'ready',
          sessionId: 'session-envelope-test',
          speechEnvelopeStatus: 'unavailable',
        }}
        avatarHost={testAvatarHost()}
        buildVersion="build-test"
        connectAttempted
        audioContainerRef={audioContainerRef}
        subscribeSpeechEnvelope={() => () => undefined}
        onConnect={() => undefined}
        onDisconnect={() => undefined}
        onResumeAudio={() => undefined}
        onToggleMicrophone={() => undefined}
        onDownloadDiagnostics={() => undefined}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Open menu' }))
    await user.click(screen.getByRole('menuitemcheckbox', { name: /STATUS/ }))

    expect(screen.getByText('SPEECH ENVELOPE').nextElementSibling?.textContent).toBe('UNAVAILABLE')
    expect(screen.getByText('AVATAR').nextElementSibling?.textContent).toBe('DEGRADED')
  })

  it('keeps the icon-only microphone control coherent and persists reduced motion', async () => {
    const user = userEvent.setup()
    render(<ReviewStand avatarHost={testAvatarHost()} buildVersion="build-test" />)

    const microphone = screen.getByRole('button', { name: 'Mute microphone' })
    expect(microphone.textContent).toBe('')
    expect(microphone.getAttribute('aria-pressed')).toBe('true')
    await user.click(microphone)
    expect(screen.getByRole('button', { name: 'Unmute microphone' }).getAttribute('aria-pressed')).toBe('false')

    await user.click(screen.getByRole('button', { name: 'Open menu' }))
    await user.click(screen.getByRole('menuitemcheckbox', { name: /REDUCE MOTION/ }))
    expect(localStorage.getItem(REDUCE_MOTION_STORAGE_KEY)).toBe('true')
    expect(document.querySelector('.voice-shell')?.getAttribute('data-user-reduced-motion')).toBe('true')
  })
})
