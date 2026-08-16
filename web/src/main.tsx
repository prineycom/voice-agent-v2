import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { AvatarHostV1 } from './avatar/AvatarHost'
import { createMvpEyeModule } from './avatar/eye/MvpEyeModule'
import { VoiceSessionProvider } from './VoiceSessionContext'

const root = document.getElementById('root')
if (root === null) throw new Error('root element is missing')

const avatarHost = new AvatarHostV1([createMvpEyeModule])
const buildVersion = import.meta.env.VITE_APP_VERSION || 'development'

createRoot(root).render(
  <StrictMode>
    <VoiceSessionProvider>
      <App avatarHost={avatarHost} buildVersion={buildVersion} />
    </VoiceSessionProvider>
  </StrictMode>,
)
