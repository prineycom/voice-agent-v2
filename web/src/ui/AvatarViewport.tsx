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
  const playoutActiveRef = useRef(false)
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
      if (!interruptionActiveRef.current) {
        envelopeRef.current = null
        playoutActiveRef.current = false
      }
      interruptionActiveRef.current = true
      host.cancel(now, motion)
      return
    }
    interruptionActiveRef.current = false
    const presentationLifecycle = playoutActiveRef.current
      && (lifecycle === 'idle' || lifecycle === 'speaking')
      ? 'speaking'
      : lifecycle
    host.update({
      schemaVersion: AVATAR_CONTROL_SCHEMA_VERSION,
      timestampMs: now,
      idleSeed,
      lifecycle: presentationLifecycle,
      motion,
      trackingTarget,
      speechEnvelope: envelopeRef.current === null ? null : {
        level: envelopeRef.current.level,
        observedAtMs: envelopeRef.current.observedAtMs,
        source: 'decoded-playout',
      },
    })
  }, [host, idleSeed, lifecycle, motion, trackingTarget])

  useEffect(() => subscribeSpeechEnvelope((observation) => {
    if (lifecycleRef.current === 'interrupted') return
    playoutActiveRef.current = observation.playoutActive
    envelopeRef.current = observation.playoutActive ? observation : null
    const presentationLifecycle = observation.playoutActive
      && (lifecycleRef.current === 'idle' || lifecycleRef.current === 'speaking')
      ? 'speaking'
      : lifecycleRef.current
    const now = performance.now()
    host.update({
      schemaVersion: AVATAR_CONTROL_SCHEMA_VERSION,
      timestampMs: now,
      idleSeed,
      lifecycle: presentationLifecycle,
      motion: motionRef.current,
      trackingTarget: targetRef.current,
      speechEnvelope: envelopeRef.current === null ? null : {
        level: envelopeRef.current.level,
        observedAtMs: envelopeRef.current.observedAtMs,
        source: 'decoded-playout',
      },
    })
  }), [host, idleSeed, subscribeSpeechEnvelope])

  return <div ref={containerRef} className="avatar-viewport" data-testid="avatar-viewport" />
}
