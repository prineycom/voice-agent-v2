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
import { REDUCE_MOTION_STORAGE_KEY } from './ui/useReducedMotion'
import { historyUserText } from './ui/panels/HistoryPanel'

function testAvatarHost(onUpdate = vi.fn()): AvatarHostV1 {
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
      mount(container) {
        root = document.createElement('div')
        root.dataset.testAvatar = 'mounted'
        container.replaceChildren(root)
      },
      update: onUpdate,
      cancel: vi.fn(),
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
    expect(screen.getByLabelText('Conversation history').getAttribute('aria-hidden')).toBe('false')
    expect(screen.getByText('Расскажи, что ты видишь.')).toBeTruthy()

    await user.click(screen.getByRole('button', { name: 'Open menu' }))
    await user.click(screen.getByRole('menuitemcheckbox', { name: /STATUS/ }))
    expect(screen.getByLabelText('Detailed status').getAttribute('aria-hidden')).toBe('false')
    expect(screen.getByRole('tab', { name: 'SYSTEM' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'TIMELINE' })).toBeTruthy()
  })

  it('uses the menu disconnect action and actionable full-screen reconnect overlay', async () => {
    const user = userEvent.setup()
    render(<ReviewStand avatarHost={testAvatarHost()} buildVersion="build-test" />)

    await user.click(screen.getByRole('button', { name: 'Open menu' }))
    await user.click(screen.getByRole('menuitem', { name: 'DISCONNECT' }))
    expect(screen.getByRole('alert').textContent).toContain('CONNECTION LOST')
    await user.click(screen.getByRole('button', { name: 'RECONNECT' }))
    expect(screen.getByRole('status', { name: /READY/ })).toBeTruthy()
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
