import { useCallback, useEffect, useState } from 'react'

export const REDUCE_MOTION_STORAGE_KEY = 'voice-agent.reduce-motion.v1'

export function readReduceMotionPreference(storage: Pick<Storage, 'getItem'>): boolean {
  try {
    return storage.getItem(REDUCE_MOTION_STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

export function writeReduceMotionPreference(
  storage: Pick<Storage, 'setItem'>,
  enabled: boolean,
): void {
  try {
    storage.setItem(REDUCE_MOTION_STORAGE_KEY, enabled ? 'true' : 'false')
  } catch {
    // A blocked preference store must not prevent the voice UI from running.
  }
}

export interface ReducedMotionPreference {
  userReducedMotion: boolean
  systemReducedMotion: boolean
  toggleUserReducedMotion(): void
}

export function useReducedMotion(): ReducedMotionPreference {
  const [userReducedMotion, setUserReducedMotion] = useState(() => (
    readReduceMotionPreference(window.localStorage)
  ))
  const [systemReducedMotion, setSystemReducedMotion] = useState(() => (
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  ))

  useEffect(() => {
    const query = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    if (query === undefined) return
    const update = (event: MediaQueryListEvent) => setSystemReducedMotion(event.matches)
    query.addEventListener('change', update)
    setSystemReducedMotion(query.matches)
    return () => query.removeEventListener('change', update)
  }, [])

  const toggleUserReducedMotion = useCallback(() => {
    setUserReducedMotion((current) => {
      const next = !current
      writeReduceMotionPreference(window.localStorage, next)
      return next
    })
  }, [])

  return { userReducedMotion, systemReducedMotion, toggleUserReducedMotion }
}
