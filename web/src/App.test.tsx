import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from './App'
import { VoiceSessionProvider } from './VoiceSessionContext'

describe('Slice 6 React shell', () => {
  it('exposes simple connection, turn, transcript, response, and audio boundaries', () => {
    render(
      <VoiceSessionProvider>
        <App />
      </VoiceSessionProvider>,
    )
    expect(screen.getByRole('heading', { name: 'Приватный голосовой диалог' })).toBeTruthy()
    expect(screen.getByText('Транскрипт появится после речи.')).toBeTruthy()
    expect(screen.getByText('Ответ появится здесь и прозвучит через LiveKit.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Подключить микрофон' })).toBeTruthy()
    expect(document.querySelector('.audio-mount')).toBeTruthy()
  })
})
