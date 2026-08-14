import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { AvatarHostV1 } from './avatar/AvatarHost'
import { createStaticFallbackModule } from './avatar/StaticFallbackModule'
import { createMvpEyeModule } from './avatar/eye/MvpEyeModule'
import { ReviewStand } from './ReviewStand'
import { VoiceSessionProvider } from './VoiceSessionContext'

const root = document.getElementById('root')
if (root === null) throw new Error('root element is missing')

const avatarHost = new AvatarHostV1([
  createMvpEyeModule,
  createStaticFallbackModule,
])
const buildVersion = import.meta.env.VITE_APP_VERSION || 'development'
const reviewMode = new URLSearchParams(window.location.search).get('review') === '1'

createRoot(root).render(
  <StrictMode>
    {reviewMode ? (
      <ReviewStand avatarHost={avatarHost} buildVersion={buildVersion} />
    ) : (
      <VoiceSessionProvider>
        <App avatarHost={avatarHost} buildVersion={buildVersion} />
      </VoiceSessionProvider>
    )}
  </StrictMode>,
)
