import type { MicrophoneVisualState } from '../stateMapping'

const ACCESSIBLE_LABELS: Record<MicrophoneVisualState, string> = {
  idle: 'Mute microphone',
  listening: 'Mute microphone, listening',
  muted: 'Unmute microphone',
  error: 'Retry microphone control',
}

export function MicrophoneButton({
  visualState,
  pressed,
  available,
  transitioning,
  onToggle,
}: {
  visualState: MicrophoneVisualState
  pressed: boolean
  available: boolean
  transitioning: boolean
  onToggle(): void
}) {
  const label = available ? ACCESSIBLE_LABELS[visualState] : 'Microphone unavailable'
  return (
    <button
      type="button"
      className={`microphone-button microphone-button--${visualState}`}
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      aria-busy={transitioning}
      disabled={!available}
      onClick={onToggle}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 14.5a3.5 3.5 0 0 0 3.5-3.5V5a3.5 3.5 0 1 0-7 0v6a3.5 3.5 0 0 0 3.5 3.5Z" />
        <path d="M5.8 10.6v.4a6.2 6.2 0 0 0 12.4 0v-.4M12 17.2V21M8.5 21h7" />
        {visualState === 'muted' && <path className="microphone-button__slash" d="M4 4l16 16" />}
      </svg>
    </button>
  )
}
