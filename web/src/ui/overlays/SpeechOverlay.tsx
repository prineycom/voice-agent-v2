export function SpeechOverlay({ text, complete }: { text: string; complete: boolean }) {
  return (
    <div
      className={`speech-overlay${text ? ' speech-overlay--visible' : ''}${complete ? ' speech-overlay--complete' : ''}`}
      aria-live="polite"
      aria-atomic="true"
    >
      {text && <p>{text}</p>}
    </div>
  )
}
