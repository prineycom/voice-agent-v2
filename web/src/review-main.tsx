import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { AvatarHostV1 } from './avatar/AvatarHost'
import { createStaticFallbackModule } from './avatar/StaticFallbackModule'
import { createMvpEyeModule } from './avatar/eye/MvpEyeModule'
import { ReviewStand } from './ReviewStand'
import './styles.css'

const root = document.getElementById('root')
if (root === null) throw new Error('root element is missing')

const avatarHost = new AvatarHostV1([
  createMvpEyeModule,
  createStaticFallbackModule,
])
const buildVersion = import.meta.env.VITE_APP_VERSION || 'development'

createRoot(root).render(
  <StrictMode>
    <ReviewStand avatarHost={avatarHost} buildVersion={buildVersion} />
  </StrictMode>,
)
