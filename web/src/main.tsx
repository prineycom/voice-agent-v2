import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { VoiceSessionProvider } from './VoiceSessionContext'

const root = document.getElementById('root')
if (root === null) throw new Error('root element is missing')

createRoot(root).render(
  <StrictMode>
    <VoiceSessionProvider>
      <App />
    </VoiceSessionProvider>
  </StrictMode>,
)
