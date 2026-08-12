import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App, { historyUserText } from './App'
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
    expect(screen.getByText('Метрики не подтверждают физическую слышимость.', { exact: false })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Подключить микрофон' })).toBeTruthy()
    expect(document.querySelector('.audio-mount')).toBeTruthy()
  })
})
