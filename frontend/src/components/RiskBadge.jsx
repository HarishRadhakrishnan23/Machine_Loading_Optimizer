import clsx from 'clsx'

const STYLES = {
  SAFE: 'bg-status-safeBg text-status-safe ring-1 ring-inset ring-green-200',
  AT_RISK: 'bg-status-warnBg text-status-warn ring-1 ring-inset ring-amber-200',
  BREACH: 'bg-status-riskBg text-status-risk ring-1 ring-inset ring-red-200',
}

const LABELS = {
  SAFE: 'Safe',
  AT_RISK: 'At Risk',
  BREACH: 'Breach',
}

const DOT = {
  SAFE: 'bg-status-safe',
  AT_RISK: 'bg-status-warn',
  BREACH: 'bg-status-risk',
}

/** Small colored pill for SAFE / AT_RISK / BREACH — used across all views for one-glance risk. */
export default function RiskBadge({ flag, size = 'md' }) {
  if (!flag) return <span className="text-text-faint text-xs">—</span>
  return (
    <span
      className={clsx(
        'badge',
        STYLES[flag] || 'bg-ink-700 text-text-muted',
        size === 'sm' && 'px-2 py-0 text-[11px]',
      )}
    >
      <span className={clsx('h-1.5 w-1.5 rounded-full', DOT[flag])} />
      {LABELS[flag] || flag}
    </span>
  )
}
