import { useEffect, useRef } from 'react'
import { AvatarHostV1 } from '../avatar/AvatarHost'
import {
  AVATAR_CONTROL_SCHEMA_VERSION,
  type AvatarHealthV1,
  type AvatarLifecycleState,
  type AvatarMotionPreference,
  type AvatarTrackingTargetV1,
} from '../avatar/contract'
import type { SpeechEnvelopeObservation } from '../playback'

interface AvatarViewportProps {
  host: AvatarHostV1
  lifecycle: AvatarLifecycleState
  motion: AvatarMotionPreference
  subscribeSpeechEnvelope(listener: (observation: SpeechEnvelopeObservation) => void): () => void
  onHealth(health: AvatarHealthV1): void
  trackingTarget?: AvatarTrackingTargetV1 | null
  idleSeed?: number
}

export function AvatarViewport({
  host,
  lifecycle,
  motion,
  subscribeSpeechEnvelope,
  onHealth,
  trackingTarget = null,
  idleSeed = 0x51ce_0007,
}: AvatarViewportProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const lifecycleRef = useRef(lifecycle)
  const motionRef = useRef(motion)
  const targetRef = useRef(trackingTarget)
  const envelopeRef = useRef<SpeechEnvelopeObservation | null>(null)
  const interruptionActiveRef = useRef(false)

  lifecycleRef.current = lifecycle
  motionRef.current = motion
  targetRef.current = trackingTarget

  useEffect(() => {
    if (containerRef.current === null) return
    host.mount(containerRef.current)
    return () => host.dispose()
  }, [host])

  useEffect(() => host.subscribeHealth(onHealth), [host, onHealth])

  useEffect(() => {
    const now = performance.now()
    if (lifecycle === 'interrupted') {
      if (!interruptionActiveRef.current) envelopeRef.current = null
      interruptionActiveRef.current = true
      host.cancel(now, motion)
      return
    }
    interruptionActiveRef.current = false
    host.update({
      schemaVersion: AVATAR_CONTROL_SCHEMA_VERSION,
      timestampMs: now,
      idleSeed,
      lifecycle,
      motion,
      trackingTarget,
      speechEnvelope: envelopeRef.current === null ? null : {
        ...envelopeRef.current,
        source: 'decoded-playout',
      },
    })
  }, [host, idleSeed, lifecycle, motion, trackingTarget])

  useEffect(() => subscribeSpeechEnvelope((observation) => {
    envelopeRef.current = observation
    if (lifecycleRef.current !== 'speaking') return
    const now = performance.now()
    host.update({
      schemaVersion: AVATAR_CONTROL_SCHEMA_VERSION,
      timestampMs: now,
      idleSeed,
      lifecycle: lifecycleRef.current,
      motion: motionRef.current,
      trackingTarget: targetRef.current,
      speechEnvelope: {
        ...observation,
        source: 'decoded-playout',
      },
    })
  }), [host, idleSeed, subscribeSpeechEnvelope])

  return <div ref={containerRef} className="avatar-viewport" data-testid="avatar-viewport" />
}
