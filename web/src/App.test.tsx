import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App, { historyUserText, MicrophoneControl } from './App'
import { VoiceSessionProvider } from './VoiceSessionContext'

describe('Slice 6 React shell', () => {
  it('labels terminal STT failure without inventing conversation content', () => {
    expect(historyUserText({
      turnId: 'turn-00000001',
      user: '',
      assistant: '',
      outcome: 'failed',
      audioUnavailable: false,
      endpointToFirstVisibleMs: null,
      endpointToFirstAcceptedPcmMs: null,
    })).toBe('Речь не распознана')
  })

  it('exposes simple connection, turn, transcript, response, and audio boundaries', () => {
    render(
      <VoiceSessionProvider>
        <App />
      </VoiceSessionProvider>,
    )
    expect(screen.getByRole('heading', { name: 'Приватный голосовой диалог' })).toBeTruthy()
    expect(screen.getByText('История появится после речи.')).toBeTruthy()
    expect(screen.getByText('Микрофон остаётся mono 16 kHz; agent PCM идёт mono 48 kHz', { exact: false })).toBeTruthy()
    expect(screen.getByText('Метрики не подтверждают физическую слышимость.', { exact: false })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Подключить микрофон' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Микрофон:/ })).toBeNull()
    expect(document.querySelector('.audio-mount')).toBeTruthy()
  })

  it('reports effective microphone state with accessible pressed semantics and styling', async () => {
    const onToggle = vi.fn()
    const { rerender } = render(
      <MicrophoneControl enabled transitioning={false} onToggle={onToggle} />,
    )
    const enabled = screen.getByRole('button', { name: 'Микрофон: включён' })
    expect(enabled.getAttribute('aria-pressed')).toBe('true')
    expect(enabled.classList.contains('microphone-on')).toBe(true)

    await userEvent.click(enabled)
    expect(onToggle).toHaveBeenCalledTimes(1)

    rerender(<MicrophoneControl enabled={false} transitioning onToggle={onToggle} />)
    const disabled = screen.getByRole('button', { name: 'Микрофон: выключен…' })
    expect(disabled.getAttribute('aria-pressed')).toBe('false')
    expect(disabled.getAttribute('aria-busy')).toBe('true')
    expect(disabled.classList.contains('microphone-off')).toBe(true)
  })
})
