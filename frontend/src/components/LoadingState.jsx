/** Shared loading / empty / error states so every view looks consistent. */
export function Spinner({ className = 'h-5 w-5' }) {
  return (
    <svg className={`animate-spin text-accent ${className}`} viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
      />
    </svg>
  )
}

export function LoadingPanel({ label = 'Loading…' }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-text-muted">
      <Spinner className="h-8 w-8" />
      <span className="text-sm">{label}</span>
    </div>
  )
}

export function EmptyPanel({ title, hint, action }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
      <div className="h-12 w-12 rounded-full bg-ink-700 flex items-center justify-center text-text-faint text-xl">
        ○
      </div>
      <p className="text-sm font-medium text-text">{title}</p>
      {hint && <p className="text-xs text-text-faint max-w-sm">{hint}</p>}
      {action}
    </div>
  )
}

export function ErrorPanel({ message, onRetry }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
      <div className="h-12 w-12 rounded-full bg-status-riskBg flex items-center justify-center text-status-risk text-xl">
        !
      </div>
      <p className="text-sm font-medium text-text">Something went wrong</p>
      <p className="text-xs text-text-muted max-w-md">{message}</p>
      {onRetry && (
        <button className="btn-secondary mt-2" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  )
}
